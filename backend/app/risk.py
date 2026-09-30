"""Gestão de risco: tamanho da posição, stop e trava de perda mensal."""
from __future__ import annotations

from dataclasses import dataclass

from .config import StrategyParams


def stop_price(entry: float, atr_value: float, p: StrategyParams) -> float:
    return max(entry - p.atr_stop_mult * atr_value, entry * 0.5)


def position_size(equity: float, entry: float, stop: float, p: StrategyParams) -> float:
    """Quantidade de moeda para que bater no stop custe no máximo RISK_PER_TRADE do capital.
    Nunca passa do saldo disponível (sem alavancagem)."""
    risk_per_unit = entry - stop
    if risk_per_unit <= 0 or equity <= 0:
        return 0.0
    qty_by_risk = (equity * p.risk_per_trade) / risk_per_unit
    qty_by_cash = equity / (entry * (1 + p.fee_rate))
    return max(0.0, min(qty_by_risk, qty_by_cash))


@dataclass
class MonthlyGuard:
    """Se o patrimônio cair MAX_MONTHLY_LOSS desde o início do mês, bloqueia até o mês seguinte."""
    max_loss: float
    month: str = ""
    start_equity: float = 0.0
    locked: bool = False

    def update(self, month: str, equity: float) -> bool:
        if month != self.month:
            self.month, self.start_equity, self.locked = month, equity, False
        if not self.locked and self.start_equity > 0:
            if equity <= self.start_equity * (1 - self.max_loss):
                self.locked = True
        return self.locked
