"""Pesquisa: rotação por momentum entre BTC, ETH e SOL, com filtro de tendência e controle de volatilidade.
Regras simples e fixas (poucos parâmetros) para não decorar o passado."""
import numpy as np, pandas as pd, itertools, json, sys

FEE = 0.001 + 0.0005  # taxa 0,1% + derrapagem 0,05% por lado

def carregar(sym, tf="4h"):
    df = pd.read_csv(f"data/binance_{sym}-USDT_{tf}_2020-01-01_hoje.csv", parse_dates=["timestamp"]).set_index("timestamp")
    return df["close"]

def diario():
    px = pd.concat({s: carregar(s) for s in ["BTC", "ETH", "SOL"]}, axis=1)
    return px.resample("1D").last()

def metricas(eq):
    r = eq.pct_change().fillna(0)
    anos = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / anos) - 1
    dd = (eq / eq.cummax() - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0
    m = eq.resample("ME").last().pct_change().dropna()
    anual = eq.resample("YE").last().pct_change()
    anual.iloc[0] = eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1
    return dict(cagr=cagr, dd=dd, sharpe=sharpe, meses_pos=(m > 0).mean(), pior_mes=m.min(),
                anos={str(k.year): round(v * 100, 1) for k, v in anual.items()})

def rotacao(px, look=60, topk=1, sma=100, alvo_vol=None, rebal=7, inicio="2021-01-01"):
    """Toda semana: fica nas `topk` moedas com maior retorno em `look` dias, só se estiverem acima da média de `sma` dias.
    Se nenhuma passar, fica em dólar. Com alvo_vol, reduz a exposição quando a volatilidade está alta."""
    ret = px.pct_change()
    mom = px / px.shift(look) - 1
    acima = px > px.rolling(sma).mean()
    vol = ret.rolling(30).std() * np.sqrt(365)
    pesos = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    atual = pd.Series(0.0, index=px.columns)
    for i, t in enumerate(px.index):
        if i % rebal == 0:
            ok = mom.loc[t][acima.loc[t] & (mom.loc[t] > 0)].dropna().sort_values(ascending=False)
            esc = list(ok.index[:topk])
            atual = pd.Series(0.0, index=px.columns)
            for s in esc:
                w = 1 / topk
                if alvo_vol and vol.loc[t, s] == vol.loc[t, s]:
                    w *= min(1.0, alvo_vol / vol.loc[t, s])
                atual[s] = w
        pesos.loc[t] = atual
    pesos = pesos.shift(1).fillna(0)  # decide no fechamento, opera no dia seguinte
    giro = pesos.diff().abs().sum(axis=1).fillna(0)
    r = (pesos * ret).sum(axis=1) - giro * FEE
    r = r[r.index >= inicio]
    return (1 + r).cumprod() * 1000, pesos[pesos.index >= inicio]

if __name__ == "__main__":
    px = diario()
    bh = px[px.index >= "2021-01-01"]
    bh_eq = (bh / bh.iloc[0]).mean(axis=1) * 1000
    print("Buy & hold igual (BTC/ETH/SOL):", {k: (round(v * 100, 1) if isinstance(v, float) else v) for k, v in metricas(bh_eq).items()})
    btc_eq = bh["BTC"] / bh["BTC"].iloc[0] * 1000
    print("Buy & hold BTC:", {k: (round(v * 100, 1) if isinstance(v, float) else v) for k, v in metricas(btc_eq).items()})
    res = []
    for look, topk, sma, av in itertools.product([30, 60, 90], [1, 2], [50, 100, 200], [None, 0.5]):
        eq, _ = rotacao(px, look, topk, sma, av)
        m = metricas(eq); m.update(look=look, topk=topk, sma=sma, alvo_vol=av)
        res.append(m)
        print(f"look {look:3d} top{topk} sma{sma:3d} vol {str(av):4s} | anual {m['cagr']*100:6.1f}% dd {m['dd']*100:6.1f}% sharpe {m['sharpe']:.2f} meses+ {m['meses_pos']*100:3.0f}% | {m['anos']}", flush=True)
    json.dump(res, open("rotacao.json", "w"), indent=1, default=str)
