"""Carteira: vários robôs (um por moeda) rodando juntos.

- O capital é dividido em partes iguais entre as moedas. Cada robô arrisca 1% da SUA fatia.
- A trava de perda mensal vale para a carteira inteira: perdeu 5% no mês somando tudo, vende tudo e para.
- Um erro numa moeda não derruba as outras.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .bot import TradingBot
from .db import Store, now_iso
from .risk import MonthlyGuard


class Portfolio:
    def __init__(self, bots: list[TradingBot], store: Store, max_monthly_loss: float, poll_seconds: int = 60):
        if not bots:
            raise ValueError("A carteira precisa de pelo menos uma moeda.")
        self.bots, self.store, self.poll = bots, store, poll_seconds
        self.mode = bots[0].broker.mode
        self.task: asyncio.Task | None = None
        self.last_error = ""
        self.last_tick = ""
        self._last_equity_at = None
        g = store.get(f"guard_{self.mode}") or {}
        self.guard = MonthlyGuard(max_monthly_loss, g.get("month", ""), g.get("start_equity", 0.0),
                                  g.get("locked", False))

    @property
    def symbols(self) -> list[str]:
        return [b.symbol for b in self.bots]

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def _save_guard(self) -> None:
        self.store.set(f"guard_{self.mode}", {"month": self.guard.month, "start_equity": self.guard.start_equity,
                                              "locked": self.guard.locked})

    # ---------- ciclo ----------
    def tick(self) -> None:
        prices: dict[str, float] = {}
        errors = []
        # 1) preço e stop de cada moeda
        for b in self.bots:
            try:
                prices[b.symbol] = b.broker.price()
                b.check_stop(prices[b.symbol])
                b.last_error = ""
            except Exception as e:  # noqa: BLE001
                b.last_error = str(e)
                errors.append(f"[{b.symbol}] {e}")
        if not prices:
            raise RuntimeError("; ".join(errors) or "sem preços")

        # 2) patrimônio total da carteira
        quote, assets, equity = 0.0, {}, 0.0
        for i, b in enumerate(b for b in self.bots if b.symbol in prices):
            q, base = b.broker.balances()
            if i == 0:
                quote = q  # todas as moedas usam a mesma moeda de cotação (ex: USDT)
            assets[b.broker.base] = base
            equity += base * prices[b.symbol]
        equity += quote
        self.store.set(f"wallet_{self.mode}", {"quote": quote, "assets": assets, "equity": equity,
                                               "prices": prices})
        now = datetime.now(timezone.utc)
        if self._last_equity_at is None or (now - self._last_equity_at).total_seconds() >= 600:
            self.store.add_equity(self.mode, equity)  # grava a cada 10 min
            self._last_equity_at = now

        # 3) trava mensal da carteira inteira
        was_locked = self.guard.locked
        if self.guard.update(now.strftime("%Y-%m"), equity) and not was_locked:
            self.store.log("Trava mensal ativada: a carteira perdeu o limite do mês. Vendendo tudo.", "warn")
            for b in self.bots:
                if b.position and b.symbol in prices:
                    b.sell(prices[b.symbol], "trava de perda mensal")
        self._save_guard()

        # 4) decisões: cada moeda recebe uma fatia igual do patrimônio
        allocation = equity / len(self.bots)
        for b in self.bots:
            if b.symbol not in prices:
                continue
            try:
                b.on_candle(prices[b.symbol], allocation, self.guard.locked)
            except Exception as e:  # noqa: BLE001
                b.last_error = str(e)
                errors.append(f"[{b.symbol}] {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    async def _loop(self) -> None:
        self.store.log(f"Robô iniciado em modo {self.mode.upper()} com {', '.join(self.symbols)}.")
        while True:
            try:
                await asyncio.to_thread(self.tick)
                self.last_error = ""
            except Exception as e:  # noqa: BLE001 - o robô não pode morrer por erro de rede
                self.last_error = str(e)
                self.store.log(f"Erro: {e}", "error")
            self.last_tick = now_iso()
            await asyncio.sleep(self.poll)

    def start(self) -> None:
        if not self.running:
            self.task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            self.store.log("Robô parado.")

    async def panic(self) -> None:
        """Botão de pânico: para tudo e vende todas as moedas na hora."""
        await self.stop()
        for b in self.bots:
            if b.position:
                try:
                    price = await asyncio.to_thread(b.broker.price)
                    await asyncio.to_thread(b.sell, price, "botão de pânico")
                except Exception as e:  # noqa: BLE001
                    b.log(f"Falha ao vender no pânico: {e}", "error")
        self.store.log("BOTÃO DE PÂNICO acionado.", "warn")

    def status(self) -> list[dict]:
        out = []
        for b in self.bots:
            out.append({"symbol": b.symbol, "position": b.position, "market": self.store.get(b.k("market")),
                        "last_error": b.last_error})
        return out
