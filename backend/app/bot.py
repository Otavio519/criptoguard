"""Robô de UMA moeda. Quem liga, desliga e divide o capital é a carteira (portfolio.py).

As regras são as mesmas do backtest (classe Decider), então o que você testa é o que roda.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import StrategyParams
from .data import _to_df
from .db import Store, now_iso
from .risk import position_size, stop_price
from .strategy import EXIT_REASON, TREND, Decider, add_indicators


def slug(symbol: str) -> str:
    return symbol.replace("/", "")


class TradingBot:
    def __init__(self, broker, exchange, symbol: str, timeframe: str, params: StrategyParams, store: Store):
        params.validate()
        self.broker, self.ex, self.symbol, self.tf = broker, exchange, symbol, timeframe
        self.p, self.store = params, store
        self.last_error = ""
        self.k = lambda name: f"{name}_{broker.mode}_{slug(symbol)}"
        self.decider = Decider(**(store.get(self.k("decider")) or {}))

    # ---------- estado ----------
    @property
    def position(self) -> dict | None:
        return self.store.get(self.k("position"))

    def _set_position(self, pos: dict | None) -> None:
        self.store.set(self.k("position"), pos)

    def log(self, msg: str, level: str = "info") -> None:
        self.store.log(f"[{self.symbol}] {msg}", level)

    def _save_decider(self) -> None:
        self.store.set(self.k("decider"), self.decider.state())

    # ---------- dados ----------
    def closed_candles(self):
        # folga grande para o ADX (suavização exponencial) convergir
        limit = min(self.p.warmup * 2 + 50, 1000)
        rows = self.ex.fetch_ohlcv(self.symbol, self.tf, limit=limit + 1)
        df = _to_df(rows)
        tf_ms = self.ex.parse_timeframe(self.tf) * 1000
        now_ms = self.ex.milliseconds()
        last_open_ms = int(df.index[-1].timestamp() * 1000)
        if last_open_ms + tf_ms > now_ms:  # descarta o candle ainda aberto
            df = df.iloc[:-1]
        return df

    # ---------- compra e venda ----------
    def _buy(self, price: float, atr_value: float, tag: str, regime: str, allocation: float) -> None:
        quote, _ = self.broker.balances()
        stp = stop_price(price, atr_value, self.p)
        # risco calculado sobre a fatia desta moeda, nunca além do dinheiro livre
        qty = position_size(min(allocation, quote), price, stp, self.p)
        if qty <= 0:
            self.log("Sinal de compra, mas não há saldo livre para esta moeda.", "warn")
            return
        filled, avg, fee = self.broker.buy(qty, price)
        pos = {"qty": filled, "entry_price": avg, "stop": stp, "entry_time": now_iso(), "peak": avg,
               "cost": filled * avg + fee, "strategy": tag, "regime": regime, "stop_order_id": None}
        self._set_position(pos)
        self.store.add_trade(self.broker.mode, "compra", avg, filled, fee, None, f"{tag} (regime {regime})",
                             self.symbol)
        self.log(f"COMPRA {filled:.6f} a {avg:.2f} pela estratégia de {tag}. Stop em {stp:.2f}.")
        self._place_exchange_stop(pos)

    def sell(self, price: float, reason: str) -> None:
        pos = self.position
        if not pos:
            return
        if pos.get("stop_order_id"):
            try:
                self.broker.cancel_stop(pos["stop_order_id"])
            except Exception:  # noqa: BLE001 - pode ter executado no meio do caminho
                pass
            st = self.broker.stop_status(pos["stop_order_id"])
            if st["filled"] > 0:
                self._record_exchange_stop_fill(pos, st)
                pos = self.position
                if not pos:
                    return
            pos["stop_order_id"] = None
            self._set_position(pos)
        filled, avg, fee = self.broker.sell(pos["qty"], price)
        pnl = filled * avg - fee - pos["cost"] * (filled / pos["qty"])
        self._set_position(None)
        self.store.add_trade(self.broker.mode, "venda", avg, filled, fee, pnl, reason, self.symbol)
        self.log(f"VENDA {filled:.6f} a {avg:.2f} ({reason}). Resultado: {pnl:+.2f}.")

    # ---------- stop dentro da corretora ----------
    def _place_exchange_stop(self, pos: dict) -> None:
        if not getattr(self.broker, "exchange_stop", False):
            return
        now = datetime.now(timezone.utc).timestamp()
        if pos.get("stop_retry_after", 0) > now:
            return
        try:
            pos["stop_order_id"] = self.broker.place_stop(pos["qty"], pos["stop"])
            self._set_position(pos)
            self.log(f"Stop registrado NA CORRETORA em {pos['stop']:.2f}. "
                     "Ele funciona mesmo com o computador desligado.")
        except Exception as e:  # noqa: BLE001
            pos["stop_retry_after"] = now + 1800  # tenta de novo em 30 min
            self._set_position(pos)
            self.log(f"Não consegui registrar o stop na corretora ({e}). "
                     "O robô vai vigiar o stop, então deixe o computador ligado.", "error")

    def _record_exchange_stop_fill(self, pos: dict, st: dict, reason: str = "stop na corretora") -> None:
        filled = min(st["filled"], pos["qty"])
        pnl = filled * st["avg"] - st["fee"] - pos["cost"] * (filled / pos["qty"])
        self.store.add_trade(self.broker.mode, "venda", st["avg"], filled, st["fee"], pnl, reason, self.symbol)
        rest = pos["qty"] - filled
        if rest * st["avg"] < 1:  # sobra desprezível
            self._set_position(None)
        else:
            pos["cost"] -= pos["cost"] * (filled / pos["qty"])
            pos["qty"], pos["stop_order_id"] = rest, None
            self._set_position(pos)
        self.log(f"VENDA {filled:.6f} a {st['avg']:.2f} ({reason}). Resultado: {pnl:+.2f}.")
        self.decider.on_stop(pos.get("strategy", TREND))
        self._save_decider()

    def _check_exchange_stop(self, pos: dict, price: float) -> dict | None:
        st = self.broker.stop_status(pos["stop_order_id"])
        if st["status"] == "closed" or (st["filled"] > 0 and st["status"] != "open"):
            self._record_exchange_stop_fill(pos, st)
            pos = self.position
            if pos:  # executou só uma parte: recoloca o stop no resto
                self._place_exchange_stop(pos)
            return self.position
        if st["status"] in ("canceled", "expired", "rejected"):
            self.log("A ordem de stop sumiu da corretora. Registrando de novo.", "warn")
            pos["stop_order_id"] = None
            self._set_position(pos)
            self._place_exchange_stop(pos)
            return self.position
        # preço despencou além do limite e a ordem não executou: vende a mercado
        if price < pos["stop"] * (1 - 2 * getattr(self.broker, "STOP_LIMIT_GAP", 0.01)):
            self.sell(price, "stop (preço passou do limite)")
            self.decider.on_stop(pos.get("strategy", TREND))
            self._save_decider()
            return None
        return pos

    def _raise_stop(self, pos: dict, new_stop: float) -> None:
        """Stop móvel: sobe o stop (nunca desce). No modo real, troca a ordem na corretora."""
        old = pos["stop"]
        if pos.get("stop_order_id"):
            try:
                self.broker.cancel_stop(pos["stop_order_id"])
                st = self.broker.stop_status(pos["stop_order_id"])
            except Exception as e:  # noqa: BLE001
                self.log(f"Não consegui subir o stop na corretora ({e}).", "warn")
                return
            if st["filled"] > 0:  # executou antes de conseguirmos trocar
                self._record_exchange_stop_fill(pos, st)
                return
            pos["stop_order_id"] = None
        pos["stop"] = new_stop
        pos.pop("stop_retry_after", None)
        self._set_position(pos)
        self.log(f"Stop móvel subiu de {old:.2f} para {new_stop:.2f}.")
        self._place_exchange_stop(pos)

    # ---------- ciclo (chamado pela carteira) ----------
    def check_stop(self, price: float) -> None:
        """Roda a cada minuto: stop na corretora (modo real) ou vigiado pelo robô."""
        pos = self.position
        if pos and pos.get("stop_order_id"):
            pos = self._check_exchange_stop(pos, price)
        elif pos and getattr(self.broker, "exchange_stop", False) and price > pos["stop"]:
            self._place_exchange_stop(pos)  # posição antiga ou falha anterior
            pos = self.position
        if pos and not pos.get("stop_order_id") and price <= pos["stop"]:
            self.sell(price, "stop loss")
            self.decider.on_stop(pos.get("strategy", TREND))
            self._save_decider()

    def on_candle(self, price: float, allocation: float, locked: bool) -> None:
        """Decide só quando fecha um candle novo."""
        df = self.closed_candles()
        last_ts = df.index[-1].isoformat()
        d = add_indicators(df, self.p)
        row = d.iloc[-1]
        self.store.set(self.k("market"), {
            "regime": str(row["regime"]), "candle": last_ts, "price": price,
            "adx": None if row["adx"] != row["adx"] else round(float(row["adx"]), 1),
            "rsi": round(float(row["rsi"]), 1)})
        processed = self.store.get(self.k("last_candle"))
        if processed is None:
            self.store.set(self.k("last_candle"), last_ts)
            self.log(f"Sincronizado. Regime atual: {row['regime']}. Esperando o próximo candle ({self.tf}).")
            return
        if last_ts == processed:
            return
        self.store.set(self.k("last_candle"), last_ts)

        pos = self.position
        # stop móvel: igual ao backtest, atualizado no fechamento do candle
        if pos and self.p.trail_atr_mult > 0 and row["atr"] == row["atr"]:
            pos["peak"] = max(pos.get("peak") or pos["entry_price"], float(row["close"]))
            new_stop = pos["peak"] - self.p.trail_atr_mult * float(row["atr"])
            if new_stop > pos["stop"] * 1.001:
                self._raise_stop(pos, new_stop)
            else:
                self._set_position(pos)
            pos = self.position

        tag = pos.get("strategy", TREND) if pos else None
        action = self.decider.on_row(row, tag)
        self._save_decider()
        if action and action.startswith("buy") and not locked:
            self._buy(price, float(row["atr"]), action.split(":")[1], str(row["regime"]), allocation)
        elif action == "sell" and pos:
            self.sell(price, EXIT_REASON.get(tag, "sinal de venda"))
