"""Corretoras: simulada (paper) e real (live).

As duas têm a mesma interface, então o robô não sabe com qual está falando.
"""
from __future__ import annotations

from .db import Store


class PaperBroker:
    """Simula ordens a mercado com o preço real da corretora.

    A carteira fictícia fica no SQLite e aceita várias moedas: {"USDT": 1000, "BTC": 0.01, "ETH": 0.2}.
    Cada robô (uma moeda) usa o seu próprio PaperBroker, todos sobre a mesma carteira.
    """
    mode = "paper"
    exchange_stop = False  # na simulação o stop é vigiado pelo robô
    WALLET = "paper_wallet"

    def __init__(self, exchange, symbol: str, store: Store, start_balance: float, fee_rate: float):
        self.ex, self.symbol, self.store, self.fee = exchange, symbol, store, fee_rate
        self.base, self.quote = symbol.split("/")
        if store.get(self.WALLET) is None:
            old = store.get("paper_balance")  # formato antigo, de uma moeda só
            wallet = {self.quote: start_balance}
            if old:
                wallet = {self.quote: old.get("quote", start_balance)}
                if old.get("base"):
                    wallet[self.base] = old["base"]
            store.set(self.WALLET, wallet)

    def _wallet(self) -> dict:
        return self.store.get(self.WALLET) or {}

    def price(self) -> float:
        return float(self.ex.fetch_ticker(self.symbol)["last"])

    def balances(self) -> tuple[float, float]:
        w = self._wallet()
        return float(w.get(self.quote, 0)), float(w.get(self.base, 0))

    def buy(self, qty: float, price: float) -> tuple[float, float, float]:
        w = self._wallet()
        quote = w.get(self.quote, 0.0)
        cost = qty * price * (1 + self.fee)
        if cost > quote:
            qty = quote / (price * (1 + self.fee))
            cost = quote
        fee = qty * price * self.fee
        w[self.quote] = quote - cost
        w[self.base] = w.get(self.base, 0.0) + qty
        self.store.set(self.WALLET, w)
        return qty, price, fee

    def sell(self, qty: float, price: float) -> tuple[float, float, float]:
        w = self._wallet()
        qty = min(qty, w.get(self.base, 0.0))
        fee = qty * price * self.fee
        w[self.base] = w.get(self.base, 0.0) - qty
        w[self.quote] = w.get(self.quote, 0.0) + qty * price - fee
        self.store.set(self.WALLET, w)
        return qty, price, fee


class LiveBroker:
    """Envia ordens reais pela API da corretora (ou testnet).

    Depois de cada compra, registra uma ordem de STOP dentro da própria corretora.
    Assim o stop funciona mesmo com o computador desligado.
    """
    mode = "live"
    exchange_stop = True
    STOP_LIMIT_GAP = 0.01  # na ordem stop-limit, o preço limite fica 1% abaixo do gatilho

    def __init__(self, exchange, symbol: str):
        self.ex, self.symbol = exchange, symbol
        self.ex.load_markets()
        self.market = self.ex.market(symbol)
        self.base, self.quote = self.market["base"], self.market["quote"]

    def price(self) -> float:
        return float(self.ex.fetch_ticker(self.symbol)["last"])

    def balances(self) -> tuple[float, float]:
        """Saldo TOTAL (livre + preso em ordens). A moeda travada no stop continua sendo sua."""
        bal = self.ex.fetch_balance()
        tot = bal.get("total") or {}
        return float(tot.get(self.quote, 0) or 0), float(tot.get(self.base, 0) or 0)

    def free_base(self) -> float:
        return float((self.ex.fetch_balance().get("free") or {}).get(self.base, 0) or 0)

    def _check_min(self, qty: float, price: float) -> None:
        limits = self.market.get("limits", {})
        min_amt = (limits.get("amount") or {}).get("min") or 0
        min_cost = (limits.get("cost") or {}).get("min") or 0
        if qty < min_amt or qty * price < min_cost:
            raise ValueError(f"Ordem abaixo do mínimo da corretora (qtd {qty}, valor {qty * price:.2f}).")

    def _fill(self, order: dict, qty: float, price: float, side: str) -> tuple[float, float, float]:
        """Devolve (quantidade líquida, preço médio, taxa em moeda de cotação)."""
        filled = float(order.get("filled") or qty)
        avg = float(order.get("average") or order.get("price") or price)
        fee = order.get("fee") or {}
        cost, cur = float(fee.get("cost") or 0), fee.get("currency")
        if cur == self.base:
            fee_quote = cost * avg
            if side == "buy":
                filled -= cost  # a Binance desconta a taxa da compra na própria moeda
        else:
            fee_quote = cost
        return filled, avg, fee_quote

    def buy(self, qty: float, price: float) -> tuple[float, float, float]:
        qty = float(self.ex.amount_to_precision(self.symbol, qty))
        self._check_min(qty, price)
        return self._fill(self.ex.create_market_buy_order(self.symbol, qty), qty, price, "buy")

    def sell(self, qty: float, price: float) -> tuple[float, float, float]:
        qty = min(qty, self.free_base())
        qty = float(self.ex.amount_to_precision(self.symbol, qty))
        self._check_min(qty, price)
        return self._fill(self.ex.create_market_sell_order(self.symbol, qty), qty, price, "sell")

    # ---------- stop dentro da corretora ----------
    def stop_floor(self, price: float) -> float:
        """Menor preço de stop que a corretora aceita (filtro PERCENT_PRICE_BY_SIDE ou PERCENT_PRICE)."""
        for f in (self.market.get("info") or {}).get("filters") or []:
            if f.get("filterType") == "PERCENT_PRICE_BY_SIDE":
                return price * float(f.get("askMultiplierDown") or 0) * 1.02
            if f.get("filterType") == "PERCENT_PRICE":
                return price * float(f.get("multiplierDown") or 0) * 1.02
        return 0.0

    def place_stop(self, qty: float, stop: float) -> str:
        qty = float(self.ex.amount_to_precision(self.symbol, qty))
        trigger = float(self.ex.price_to_precision(self.symbol, stop))
        types = (self.market.get("info") or {}).get("orderTypes") or []
        if "STOP_LOSS" in types:  # vende a mercado quando o gatilho é atingido
            order = self.ex.create_order(self.symbol, "STOP_LOSS", "sell", qty, None, {"stopPrice": trigger})
        else:
            limit = float(self.ex.price_to_precision(self.symbol, stop * (1 - self.STOP_LIMIT_GAP)))
            order = self.ex.create_order(self.symbol, "STOP_LOSS_LIMIT", "sell", qty, limit,
                                         {"stopPrice": trigger, "timeInForce": "GTC"})
        return str(order["id"])

    def stop_status(self, order_id: str) -> dict:
        o = self.ex.fetch_order(order_id, self.symbol)
        filled, avg, fee = self._fill(o, 0.0, float(o.get("stopPrice") or o.get("price") or 0), "sell") \
            if float(o.get("filled") or 0) > 0 else (0.0, 0.0, 0.0)
        return {"status": o.get("status"), "filled": filled, "avg": avg, "fee": fee}

    def cancel_stop(self, order_id: str) -> None:
        self.ex.cancel_order(order_id, self.symbol)
