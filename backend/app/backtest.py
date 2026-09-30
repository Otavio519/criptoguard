"""Motor de backtest.

Regras para evitar "olhar o futuro":
- A decisão nasce no FECHAMENTO do candle t.
- A ordem executa na ABERTURA do candle t+1.
- O stop é checado pela mínima de cada candle. Se o preço abrir abaixo do stop (gap), sai pela abertura.
- Taxa cobrada na compra e na venda.
- Indicadores usam só dados passados (médias móveis e suavizações exponenciais).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .config import StrategyParams
from .risk import MonthlyGuard, position_size, stop_price
from .strategy import EXIT_REASON, Decider, add_indicators


@dataclass
class Trade:
    entry_time: str
    entry_price: float
    qty: float
    stop: float
    strategy: str
    regime: str
    equity_before: float
    exit_time: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    ret_equity: float = 0.0  # resultado sobre o patrimônio da hora da entrada
    reason: str = ""
    peak: float = 0.0


def periods_per_year(index: pd.DatetimeIndex) -> float:
    """Candles por ano de calendário. Cripto negocia 365 dias, a bolsa uns 250.
    Contar pelo calendário real evita inflar o retorno anual das ações."""
    if len(index) < 2:
        return 365.0
    span_days = (index[-1] - index[0]).total_seconds() / 86400
    if span_days <= 0:
        return 365.0
    return (len(index) - 1) / (span_days / 365.25)


def max_drawdown(equity) -> float:
    e = pd.Series(np.asarray(equity, dtype=float))
    peak = e.cummax()
    return float(((e - peak) / peak).min())


def simulate(d: pd.DataFrame, p: StrategyParams, initial: float = 1000.0) -> dict:
    """Roda a simulação em um DataFrame que JÁ tem os indicadores (add_indicators)."""
    o, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "low", "close"))
    atr_v = d["atr"].to_numpy(dtype=float)
    ready = d["ready"].to_numpy(dtype=bool)
    trend_ok = d["trend_ok"].to_numpy(dtype=bool)
    regime = d["regime"].to_numpy(dtype=object)
    e_tr, x_tr = d["entry_trend"].to_numpy(dtype=bool), d["exit_trend"].to_numpy(dtype=bool)
    e_mr, x_mr = d["entry_mr"].to_numpy(dtype=bool), d["exit_mr"].to_numpy(dtype=bool)
    times = [t.isoformat() for t in d.index]
    months = [t.strftime("%Y-%m") for t in d.index]

    cash, qty = initial, 0.0
    trade: Trade | None = None
    trades: list[Trade] = []
    guard = MonthlyGuard(p.max_monthly_loss)
    decider = Decider()
    pending: str | None = None
    equity = np.empty(len(d))
    in_market = locked_bars = 0

    def close_position(i: int, price: float, reason: str) -> None:
        nonlocal cash, qty, trade
        proceeds = qty * price * (1 - p.fee_rate)
        cost = trade.qty * trade.entry_price * (1 + p.fee_rate)
        cash += float(proceeds)
        trade.exit_time, trade.exit_price = times[i], float(price)
        trade.pnl = float(proceeds - cost)
        trade.pnl_pct = trade.pnl / cost
        trade.ret_equity = trade.pnl / trade.equity_before
        trade.reason = reason
        trades.append(trade)
        qty, trade = 0.0, None

    for i in range(len(d)):
        locked = guard.locked and guard.month == months[i]

        # 1) ordem decidida no candle anterior, executada na abertura
        if pending == "sell" and trade:
            close_position(i, o[i], EXIT_REASON[trade.strategy])
        elif pending and pending.startswith("buy") and not trade and not locked and i > 0 \
                and not np.isnan(atr_v[i - 1]):
            entry = float(o[i])
            stp = stop_price(entry, atr_v[i - 1], p)
            q = position_size(cash, entry, stp, p)
            if q > 0:
                eq_before = cash
                cash -= q * entry * (1 + p.fee_rate)
                qty = q
                trade = Trade(times[i], entry, q, stp, pending.split(":")[1], str(regime[i - 1]), eq_before)
        pending = None

        # 2) stop loss
        if trade and l[i] <= trade.stop:
            tag = trade.strategy
            close_position(i, min(o[i], trade.stop), "stop loss")
            decider.on_stop(tag)

        # 2b) stop móvel: sobe junto com o maior fechamento, nunca desce
        if trade and p.trail_atr_mult > 0 and not np.isnan(atr_v[i]):
            trade.peak = max(trade.peak or trade.entry_price, c[i])
            trade.stop = max(trade.stop, trade.peak - p.trail_atr_mult * atr_v[i])

        # 3) patrimônio no fechamento + trava mensal
        eq = cash + qty * c[i]
        if guard.update(months[i], eq) and trade:
            close_position(i, c[i], "trava de perda mensal")
            eq = cash
        locked_bars += guard.locked
        in_market += trade is not None
        equity[i] = eq

        # 4) decisão no fechamento
        pending = decider.on_bar(ready[i], trend_ok[i], regime[i], e_tr[i], x_tr[i], e_mr[i], x_mr[i],
                                 trade.strategy if trade else None)

    if trade:
        close_position(len(d) - 1, c[-1], "fim do período")
        equity[-1] = cash

    return {"equity": equity, "trades": trades, "in_market": in_market, "locked_bars": locked_bars}


def summarize(d: pd.DataFrame, sim: dict, initial: float) -> dict:
    equity, trades = sim["equity"], sim["trades"]
    c = d["close"].to_numpy(dtype=float)
    bh = initial * c / c[0]
    ppy = periods_per_year(d.index)
    eq = pd.Series(equity, index=d.index)
    rets = eq.pct_change().dropna()
    years = max(len(d) / ppy, 1e-9)
    wins = [t for t in trades if t.pnl > 0]
    gross_loss = -sum(t.pnl for t in trades if t.pnl <= 0)
    final = float(equity[-1])

    by_strat = {}
    for name in ("tendencia", "reversao"):
        ts = [t for t in trades if t.strategy == name]
        if ts:
            by_strat[name] = {"operacoes": len(ts), "resultado": round(float(sum(t.pnl for t in ts)), 2),
                              "taxa_acerto": float(sum(t.pnl > 0 for t in ts) / len(ts))}
    reg = d.loc[d["ready"], "regime"].value_counts(normalize=True)

    return {
        "capital_inicial": initial,
        "capital_final": round(final, 2),
        "retorno_total": final / initial - 1,
        "retorno_anual": (final / initial) ** (1 / years) - 1 if final > 0 else -1.0,
        "max_drawdown": max_drawdown(equity),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0,
        "operacoes": len(trades),
        "taxa_acerto": len(wins) / len(trades) if trades else 0.0,
        "fator_lucro": float(sum(t.pnl for t in wins) / gross_loss) if gross_loss > 0 else None,
        "buy_hold_retorno": float(bh[-1] / initial - 1),
        "buy_hold_drawdown": max_drawdown(bh),
        "tempo_no_mercado": sim["in_market"] / len(d),
        "candles_travados": int(sim["locked_bars"]),
        "venceu_buy_hold": bool(final > bh[-1]),
        "por_estrategia": by_strat,
        "regimes": {k: float(v) for k, v in reg.items()},
    }


def build_curve(d: pd.DataFrame, equity: np.ndarray, initial: float, max_points: int = 600) -> list[dict]:
    c = d["close"].to_numpy(dtype=float)
    bh = initial * c / c[0]
    regime = d["regime"].to_numpy(dtype=object)
    times = [t.isoformat() for t in d.index]
    idx = list(range(0, len(d), max(1, len(d) // max_points)))
    if idx[-1] != len(d) - 1:
        idx.append(len(d) - 1)
    return [{"t": times[i], "robo": round(float(equity[i]), 2), "buy_hold": round(float(bh[i]), 2),
             "preco": round(float(c[i]), 2), "regime": str(regime[i])} for i in idx]


def run_backtest(df: pd.DataFrame, p: StrategyParams, initial: float = 1000.0) -> dict:
    p.validate()
    if len(df) < p.warmup + 30:
        raise ValueError(f"Poucos candles ({len(df)}). Precisa de pelo menos {p.warmup + 30} "
                         f"(o filtro de regime usa média de {p.regime_sma}).")
    d = add_indicators(df, p)
    sim = simulate(d, p, initial)
    return {
        "metricas": summarize(d, sim, initial),
        "curva": build_curve(d, sim["equity"], initial),
        "operacoes": [asdict(t) for t in sim["trades"]],
    }
