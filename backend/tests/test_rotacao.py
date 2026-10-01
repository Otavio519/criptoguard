from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.broker import PaperBroker
from app.db import Store
from app.rotacao import RotacaoPortfolio, RotParams, escolher, indicadores, regime

DIA0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeEx:
    """Candles diários: BTC sobe firme, ETH cai. `hoje` controla até onde a série vai."""
    def __init__(self):
        n = 410
        t = np.arange(n)
        rng = np.random.default_rng(1)
        self.series = {
            "BTC/USDT": 20000 * np.exp(0.004 * t + rng.normal(0, 0.01, n).cumsum() * 0.2),
            "ETH/USDT": 3000 * np.exp(-0.002 * t + rng.normal(0, 0.01, n).cumsum() * 0.2),
        }
        self.inicio = DIA0 - timedelta(days=n - 11)
        self.hoje = DIA0

    def idx(self):
        return (self.hoje.date() - self.inicio.date()).days

    def fetch_ohlcv(self, symbol, tf, limit=100, since=None):
        i = self.idx()
        px = self.series[symbol][: i + 1]
        rows = []
        for k, p in enumerate(px):
            ts = int((self.inicio + timedelta(days=k)).timestamp() * 1000)
            rows.append([ts, p, p * 1.01, p * 0.99, p, 100.0])
        return rows[-limit:]

    def fetch_ticker(self, symbol):
        return {"last": float(self.series[symbol][self.idx()])}


def test_indicadores_e_escolha():
    up = pd.Series(np.linspace(100, 300, 260), index=pd.date_range("2025-01-01", periods=260, tz="UTC"))
    down = pd.Series(np.linspace(300, 100, 260), index=up.index)
    p = RotParams()
    iu, idn = indicadores(up, p), indicadores(down, p)
    assert regime(iu) == "alta" and regime(idn) == "baixa"
    w = escolher({"A": iu, "B": idn}, p)
    assert w["B"] == 0 and 0 < w["A"] <= 0.5
    assert indicadores(up.iloc[:50], p) == {"pronto": False}


def test_robo_compra_a_forte_e_sai_quando_perde_a_media(tmp_path):
    store = Store(tmp_path / "t.db")
    ex = FakeEx()
    brokers = [PaperBroker(ex, s, store, 1000, 0.001) for s in ex.series]
    rob = RotacaoPortfolio(brokers, ex, store, RotParams(capital=1000))
    rob.tick(DIA0.replace(hour=0, minute=10))
    c = store.get("rot_paper")
    assert c["hold"].get("BTC/USDT", 0) > 0          # comprou a moeda forte
    assert c["hold"].get("ETH/USDT", 0) == 0          # não tocou na fraca
    assert store.get("market_paper_BTCUSDT")["regime"] == "alta"
    eq = store.get("wallet_paper")["equity"]
    assert 990 < eq < 1001
    # mesmo dia: não decide de novo
    n_trades = len(store.rows("SELECT * FROM trades"))
    rob.tick(DIA0.replace(hour=5))
    assert len(store.rows("SELECT * FROM trades")) == n_trades
    # BTC despenca abaixo da média de 200 dias: sai no dia seguinte
    ex.series["BTC/USDT"][ex.idx() + 1:] = ex.series["BTC/USDT"][ex.idx()] * 0.4
    ex.hoje = DIA0 + timedelta(days=2)
    rob.tick((DIA0 + timedelta(days=2)).replace(hour=0, minute=10))
    c = store.get("rot_paper")
    assert c["hold"]["BTC/USDT"] == 0
    vendas = store.rows("SELECT * FROM trades WHERE side='venda'")
    assert vendas and ("média de 200" in vendas[-1]["reason"] or "desastre" in vendas[-1]["reason"])


def test_trava_mensal(tmp_path):
    store = Store(tmp_path / "t.db")
    ex = FakeEx()
    brokers = [PaperBroker(ex, s, store, 1000, 0.001) for s in ex.series]
    rob = RotacaoPortfolio(brokers, ex, store, RotParams(capital=1000, stop=0))
    rob.tick(DIA0.replace(hour=0, minute=10))
    # queda de 30% no BTC no mesmo mês, ainda acima da média: a trava de 10% vende tudo
    ex.series["BTC/USDT"][ex.idx():] *= 0.7
    rob.tick(DIA0.replace(hour=3))
    c = store.get("rot_paper")
    assert c["travado"] and c["hold"]["BTC/USDT"] == 0
