"""Roda o robô de verdade (RotacaoPortfolio + PaperBroker) dia a dia sobre o histórico real, para conferir a simulação."""
import sys, tempfile
from datetime import timedelta
from pathlib import Path
import pandas as pd
sys.path.insert(0, "/home/claude/criptoguard/backend")
sys.path.insert(0, "/home/claude/research")
from app.broker import PaperBroker
from app.db import Store
from app.rotacao import RotacaoPortfolio, RotParams
from rotacao import metricas
D = {s: pd.read_csv(f"/home/claude/research/data/binance_{s.split('/')[0]}-USDT_1d_2020-01-01_hoje.csv", parse_dates=["timestamp"]).set_index("timestamp") for s in ["BTC/USDT", "ETH/USDT"]}
class Ex:
    now = None
    def fetch_ohlcv(self, s, tf, limit=100, since=None):
        df = D[s][D[s].index <= self.now.normalize()]
        return [[int(t.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume] for t, r in df.tail(limit).iterrows()]
    def fetch_ticker(self, s):  # preço do momento = abertura do dia (logo após o fechamento de ontem)
        return {"last": float(D[s].loc[self.now.normalize(), "open"])}
ini = sys.argv[1] if len(sys.argv) > 1 else "2022-01-01"
ex = Ex(); store = Store(Path(tempfile.mkdtemp()) / "r.db")
rob = RotacaoPortfolio([PaperBroker(ex, s, store, 1000, 0.001) for s in D], ex, store, RotParams(capital=1000))
eq = {}
for t in D["BTC/USDT"].index[D["BTC/USDT"].index >= ini]:
    ex.now = t + timedelta(minutes=10)
    rob.tick(ex.now.to_pydatetime())
    eq[t] = store.get("wallet_paper")["equity"]
m = metricas(pd.Series(eq))
print(f"robô real desde {ini}: anual {m['cagr']*100:.1f}% queda máx {m['dd']*100:.1f}% sharpe {m['sharpe']:.2f} {m['anos']}")
print("operações:", len(store.rows("SELECT * FROM trades")))
