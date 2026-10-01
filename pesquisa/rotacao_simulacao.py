"""Simulação diária fiel ao robô: rebalanceia a cada 7 dias, sai no dia se perder a média 200,
stop de desastre na corretora (X% abaixo da entrada, usa a mínima do dia) e trava mensal opcional."""
import numpy as np, pandas as pd
from rotacao import metricas, FEE
def carregar(sym):
    df = pd.read_csv(f"data/binance_{sym}-USDT_1d_2020-01-01_hoje.csv", parse_dates=["timestamp"]).set_index("timestamp")
    return df
def sim(cols=("BTC","ETH"), look=30, topk=2, sma=200, alvo=0.5, stop_pct=None, trava=None, inicio="2021-01-01", off=0):
    d = {s: carregar(s) for s in cols}
    close = pd.concat({s: d[s]["close"] for s in cols}, axis=1).dropna()
    low = pd.concat({s: d[s]["low"] for s in cols}, axis=1).reindex(close.index)
    mom = close / close.shift(look) - 1; media = close.rolling(sma).mean(); vol = close.pct_change().rolling(30).std() * np.sqrt(365)
    cash, hold, entry = 1000.0, {s: 0.0 for s in cols}, {s: 0.0 for s in cols}
    eq = []; mes = None; ini_mes = None; travado = False; ultimo = None
    idx = close.index[close.index >= inicio][off:]
    for i, t in enumerate(idx):
        px = close.loc[t]
        # stop de desastre durante o dia
        if stop_pct:
            for s in cols:
                if hold[s] > 0 and low.loc[t, s] <= entry[s] * (1 - stop_pct):
                    p = entry[s] * (1 - stop_pct); cash += hold[s] * p * (1 - FEE); hold[s] = 0
        val = cash + sum(hold[s] * px[s] for s in cols)
        if t.month != mes: mes, ini_mes, travado = t.month, val, False
        if trava and val < ini_mes * (1 - trava) and not travado:
            travado = True
            for s in cols:
                if hold[s] > 0: cash += hold[s] * px[s] * (1 - FEE); hold[s] = 0
        rebal = ultimo is None or (t - ultimo).days >= 7
        alvo_w = None
        if rebal and not travado:
            ultimo = t
            ok = mom.loc[t][(close.loc[t] > media.loc[t]) & (mom.loc[t] > 0)].dropna().sort_values(ascending=False)
            alvo_w = {s: 0.0 for s in cols}
            for s in ok.index[:topk]:
                alvo_w[s] = (1 / topk) * min(1.0, alvo / vol.loc[t, s])
        for s in cols:  # saída diária
            if hold[s] > 0 and close.loc[t, s] < media.loc[t, s]:
                cash += hold[s] * px[s] * (1 - FEE); hold[s] = 0
                if alvo_w: alvo_w[s] = 0.0
        if alvo_w is not None:
            val = cash + sum(hold[s] * px[s] for s in cols)
            for s in cols:  # vende primeiro
                alvo_q = val * alvo_w[s] / px[s]
                if hold[s] > alvo_q * 1.1 or (alvo_w[s] == 0 and hold[s] > 0):
                    q = hold[s] - alvo_q; cash += q * px[s] * (1 - FEE); hold[s] -= q
            for s in cols:
                alvo_q = val * alvo_w[s] / px[s]
                if alvo_q > hold[s] * 1.1 and alvo_w[s] > 0:
                    q = min(alvo_q - hold[s], cash / (px[s] * (1 + FEE)))
                    if q > 0:
                        if hold[s] == 0: entry[s] = px[s]
                        else: entry[s] = (entry[s] * hold[s] + px[s] * q) / (hold[s] + q)
                        cash -= q * px[s] * (1 + FEE); hold[s] += q
        eq.append((t, cash + sum(hold[s] * px[s] for s in cols)))
    return pd.Series(dict(eq))
for stop in [None, 0.15, 0.20, 0.25]:
    for trava in [None, 0.10]:
        for ini in ["2021-01-01", "2022-01-01"]:
            r = [metricas(sim(stop_pct=stop, trava=trava, inicio=ini, off=o)) for o in range(0, 7, 2)]
            an = np.mean([m['cagr'] for m in r]); dd = np.min([m['dd'] for m in r])
            print(f"stop {stop} trava {trava} desde {ini}: anual médio {an*100:5.1f}% pior queda {dd*100:5.1f}% | {r[0]['anos']}")
