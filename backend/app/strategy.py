"""Indicadores, filtro de regime e regras de decisão.

Regimes:
  alta    -> preço acima da média de regime e ADX forte. Usa SEGUIMENTO DE TENDÊNCIA.
  lateral -> ADX fraco (sem tendência). Usa REVERSÃO À MÉDIA (Bollinger + RSI).
  baixa   -> preço abaixo da média de regime e ADX forte. Fica FORA do mercado (spot não vende a descoberto).

Sem filtro (use_regime=False) o sistema volta ao modo clássico: só cruzamento de médias.

A mesma classe `Decider` decide no backtest e no robô. Assim o que você testa é o que roda.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams

TREND, MEANREV = "tendencia", "reversao"


# ---------------- indicadores ----------------
def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """ADX de Wilder. Mede a FORÇA da tendência (não a direção)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    a = 1 / period
    atr_w = tr.ewm(alpha=a, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=a, adjust=False, min_periods=period).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=a, adjust=False, min_periods=period).mean() / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


# ---------------- indicadores + regime + sinais ----------------
def add_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    out["sma_fast"] = c.rolling(p.sma_fast).mean()
    out["sma_slow"] = c.rolling(p.sma_slow).mean()
    out["atr"] = atr(out, p.atr_period)
    out["bb_mid"] = c.rolling(p.bb_period).mean()
    std = c.rolling(p.bb_period).std()
    out["bb_low"] = out["bb_mid"] - p.bb_std * std
    out["bb_up"] = out["bb_mid"] + p.bb_std * std
    out["rsi"] = rsi(c, p.rsi_period)

    vol_ok = pd.Series(True, index=out.index)
    if p.vol_filter > 0:
        vol_ok = out["volume"] > out["volume"].rolling(20).mean() * p.vol_filter

    trend_ok = out["sma_fast"] > out["sma_slow"]
    prev_ok = trend_ok.shift(1, fill_value=False)
    cross_up = trend_ok & ~prev_ok
    cross_dn = ~trend_ok & prev_ok

    ready = out[["sma_fast", "sma_slow", "atr"]].notna().all(axis=1) & out["sma_slow"].shift(1).notna()

    if p.use_regime:
        out["sma_regime"] = c.rolling(p.regime_sma).mean()
        out["adx"] = adx(out, p.adx_period)
        strong = out["adx"] >= p.adx_min
        regime = np.where(strong & (c > out["sma_regime"]), "alta",
                          np.where(strong & (c < out["sma_regime"]), "baixa", "lateral"))
        out["regime"] = regime
        ready &= out[["sma_regime", "adx", "bb_low", "rsi"]].notna().all(axis=1)
        is_up, is_down, is_side = out["regime"] == "alta", out["regime"] == "baixa", out["regime"] == "lateral"
        out["entry_trend"] = is_up & trend_ok & vol_ok
        out["exit_trend"] = ~trend_ok | is_down
        out["entry_mr"] = is_side & (c < out["bb_low"]) & (out["rsi"] < p.rsi_buy) & p.use_meanrev
        out["exit_mr"] = (c >= out["bb_mid"]) | is_down
    else:
        out["sma_regime"] = np.nan
        out["adx"] = np.nan
        out["regime"] = "sem filtro"
        out["entry_trend"] = cross_up & vol_ok
        out["exit_trend"] = cross_dn
        out["entry_mr"] = False
        out["exit_mr"] = False

    out["trend_ok"] = trend_ok
    out["ready"] = ready
    out.loc[~ready, "regime"] = "aquecendo"
    # Coluna de sinal simples (compatibilidade e gráficos): 1 compra, -1 venda
    out["signal"] = 0
    out.loc[ready & cross_up, "signal"] = 1
    out.loc[ready & cross_dn, "signal"] = -1
    return out


class Decider:
    """Decide a ação no fechamento de cada candle.

    Trava de reentrada: depois de um stop na estratégia de tendência, o robô só volta a
    comprar quando a tendência 'reiniciar' (média rápida volta para baixo da lenta ou o regime muda).
    Isso evita comprar de novo logo depois de tomar stop.
    Na reversão à média vale o mesmo: depois de um stop, espera o preço sair da zona de compra.
    """

    def __init__(self, rearm: bool = True, rearm_mr: bool = True):
        self.rearm = rearm
        self.rearm_mr = rearm_mr

    def state(self) -> dict:
        return {"rearm": self.rearm, "rearm_mr": self.rearm_mr}

    def on_bar(self, ready: bool, trend_ok: bool, regime: str, entry_trend: bool, exit_trend: bool,
               entry_mr: bool, exit_mr: bool, pos_tag: str | None) -> str | None:
        if not ready:
            return None
        if not trend_ok or regime not in ("alta", "sem filtro"):
            self.rearm = True
        if not entry_mr:
            self.rearm_mr = True
        if pos_tag == TREND:
            return "sell" if exit_trend else None
        if pos_tag == MEANREV:
            return "sell" if exit_mr else None
        if entry_trend and self.rearm:
            return f"buy:{TREND}"
        if entry_mr and self.rearm_mr:
            return f"buy:{MEANREV}"
        return None

    def on_stop(self, tag: str) -> None:
        if tag == TREND:
            self.rearm = False
        elif tag == MEANREV:
            self.rearm_mr = False

    def on_row(self, row, pos_tag: str | None) -> str | None:
        return self.on_bar(bool(row["ready"]), bool(row["trend_ok"]), str(row["regime"]),
                           bool(row["entry_trend"]), bool(row["exit_trend"]),
                           bool(row["entry_mr"]), bool(row["exit_mr"]), pos_tag)


EXIT_REASON = {TREND: "saída da tendência", MEANREV: "voltou para a média"}
