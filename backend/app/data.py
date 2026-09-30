"""Coleta de candles (OHLCV) pela biblioteca ccxt, com cache em CSV."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from .config import DATA_DIR

CACHE_DIR = DATA_DIR / "cache"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def make_exchange(name: str, api_key: str = "", secret: str = "", testnet: bool = False):
    if not hasattr(ccxt, name):
        raise ValueError(f"Corretora '{name}' não existe no ccxt.")
    ex = getattr(ccxt, name)({
        "apiKey": api_key or None,
        "secret": secret or None,
        "enableRateLimit": True,
        "options": {"defaultType": "spot", "fetchMarkets": {"types": ["spot"]}, "fetchCurrencies": False,
                    "adjustForTimeDifference": True, "recvWindow": 10000},
    })
    if testnet and hasattr(ex, "set_sandbox_mode"):
        ex.set_sandbox_mode(True)
    return ex


def _to_df(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")
    return df.astype(float)


def fetch_ohlcv(exchange_name: str, symbol: str, timeframe: str, since: str,
                until: str | None = None, exchange=None) -> pd.DataFrame:
    """Baixa candles paginando de 1000 em 1000. Datas no formato AAAA-MM-DD."""
    ex = exchange or make_exchange(exchange_name)
    start_ms = ex.parse8601(f"{since}T00:00:00Z")
    end_ms = ex.parse8601(f"{until}T00:00:00Z") if until else ex.milliseconds()
    step = ex.parse_timeframe(timeframe) * 1000
    rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(r for r in batch if r[0] < end_ms)
        last = batch[-1][0]
        if last + step <= cursor:
            break
        cursor = last + step
        time.sleep(ex.rateLimit / 1000)
    if not rows:
        raise RuntimeError("A corretora não devolveu candles para esse período.")
    return _to_df(rows)


def load_or_fetch(exchange_name: str, symbol: str, timeframe: str, since: str,
                  until: str | None = None) -> pd.DataFrame:
    """Usa o CSV em cache quando existe. Senão baixa e salva."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{exchange_name}_{symbol.replace('/', '-')}_{timeframe}_{since}_{until or 'hoje'}.csv"
    path = CACHE_DIR / tag
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fresh = path.exists() and (until or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d") == today)
    if fresh:
        return pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
    df = fetch_ohlcv(exchange_name, symbol, timeframe, since, until)
    df.to_csv(path)
    return df


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp")[COLUMNS[1:]].astype(float)


def synthetic_ohlcv(days: int = 1500, seed: int = 42, start_price: float = 20000.0) -> pd.DataFrame:
    """Preços falsos com tendências, para testes e demonstração offline."""
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-0.002, 0.0005, 0.003], size=days // 60 + 1), 60)[:days]
    rets = regime + rng.normal(0, 0.03, days)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]]) * (1 + rng.normal(0, 0.003, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.012, days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.012, days)))
    idx = pd.date_range("2021-01-01", periods=days, freq="D", tz="UTC", name="timestamp")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(100, 1000, days)}, index=idx)
