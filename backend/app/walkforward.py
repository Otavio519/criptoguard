"""Validação walk-forward.

Como funciona:
  |---- treino (otimiza) ----|-- teste --|
             |---- treino (otimiza) ----|-- teste --|
                        |---- treino (otimiza) ----|-- teste --|

1. Em cada janela de TREINO, testa todas as combinações de parâmetros e escolhe a melhor.
2. Aplica essa combinação na janela de TESTE seguinte, que o otimizador nunca viu.
3. Emenda só os resultados das janelas de teste. Esse é o resultado honesto da estratégia.

Se a melhor combinação do treino der prejuízo, o sistema fica em caixa na janela de teste.
"""
from __future__ import annotations

import itertools
from collections import Counter
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from .backtest import max_drawdown, periods_per_year, simulate
from .config import StrategyParams
from .montecarlo import monte_carlo
from .strategy import add_indicators

DEFAULT_GRID: dict[str, list] = {
    "sma_fast": [10, 20, 30],
    "sma_slow": [50, 100],
    "atr_stop_mult": [1.5, 2.0, 3.0],
    "adx_min": [15, 20, 25],
}
MAX_COMBOS = 300


def _combos(base: StrategyParams, grid: dict[str, list]) -> list[StrategyParams]:
    keys = list(grid)
    out = []
    for values in itertools.product(*(grid[k] for k in keys)):
        p = replace(base, **dict(zip(keys, values)))
        try:
            p.validate()
        except ValueError:
            continue
        out.append(p)
    if not out:
        raise ValueError("Nenhuma combinação válida na grade de parâmetros.")
    if len(out) > MAX_COMBOS:
        raise ValueError(f"Grade grande demais ({len(out)} combinações). Limite: {MAX_COMBOS}.")
    return out


def _score(equity: np.ndarray, n_trades: int, min_trades: int) -> tuple[float, float]:
    """Nota = retorno / pior queda (estilo Calmar). Poucas operações = nota ruim."""
    ret = equity[-1] / equity[0] - 1
    if n_trades < min_trades:
        return -np.inf, ret
    dd = abs(max_drawdown(equity))
    return ret / max(dd, 0.02), ret


def walk_forward(df: pd.DataFrame, base: StrategyParams, initial: float = 1000.0,
                 train_bars: int = 365, test_bars: int = 90, grid: dict | None = None,
                 min_trades: int = 3) -> dict:
    base.validate()
    grid = dict(grid or DEFAULT_GRID)
    if not base.use_regime:
        grid.pop("adx_min", None)  # sem filtro de regime, o ADX não muda nada
    combos = _combos(base, grid)
    warm = max(p.warmup for p in combos)
    n = len(df)
    if n < warm + train_bars + test_bars:
        raise ValueError(f"Dados insuficientes: {n} candles. Precisa de {warm + train_bars + test_bars} "
                         f"({warm} de aquecimento + {train_bars} de treino + {test_bars} de teste).")

    # Indicadores calculados uma vez por combinação no histórico todo.
    # Não há vazamento: cada valor usa só candles anteriores.
    frames = [add_indicators(df, p) for p in combos]

    windows = []
    oos_equity: list[np.ndarray] = []
    oos_index: list[pd.Index] = []
    oos_trades = []
    capital = initial
    is_annual = []
    ppy = periods_per_year(df.index)
    chosen = Counter()

    start = warm
    while start + train_bars + max(10, test_bars // 3) <= n:
        tr = slice(start, start + train_bars)
        te = slice(start + train_bars, min(start + train_bars + test_bars, n))

        best_k, best_score, best_ret = -1, -np.inf, -np.inf
        for k, (p, f) in enumerate(zip(combos, frames)):
            sim = simulate(f.iloc[tr], p, 1000.0)
            sc, ret = _score(sim["equity"], len(sim["trades"]), min_trades)
            if sc > best_score:
                best_k, best_score, best_ret = k, sc, ret

        test_df = frames[max(best_k, 0)].iloc[te]
        closes = test_df["close"].to_numpy(dtype=float)
        if best_k < 0 or best_ret <= 0:
            eq = np.full(len(test_df), capital)
            params_txt, trades_w, action = "fica em caixa", [], "caixa"
        else:
            p = combos[best_k]
            sim = simulate(test_df, p, capital)
            eq, trades_w, action = sim["equity"], sim["trades"], "opera"
            params_txt = f"MM {p.sma_fast}/{p.sma_slow} · stop {p.atr_stop_mult}xATR" + \
                         (f" · ADX {p.adx_min:g}" if p.use_regime else "")
            is_annual.append((1 + best_ret) ** (ppy / train_bars) - 1)
        chosen[params_txt] += 1

        windows.append({
            "treino_inicio": df.index[tr.start].isoformat(),
            "teste_inicio": test_df.index[0].isoformat(),
            "teste_fim": test_df.index[-1].isoformat(),
            "parametros": params_txt,
            "acao": action,
            "retorno_treino": float(best_ret) if np.isfinite(best_ret) else None,
            "retorno_teste": float(eq[-1] / capital - 1),
            "buy_hold_teste": float(closes[-1] / closes[0] - 1),
            "operacoes": len(trades_w),
        })
        oos_equity.append(eq)
        oos_index.append(test_df.index)
        oos_trades.extend(trades_w)
        capital = float(eq[-1])
        start += test_bars

    equity = np.concatenate(oos_equity)
    idx = oos_index[0].append(oos_index[1:]) if len(oos_index) > 1 else oos_index[0]
    closes = df["close"].reindex(idx).to_numpy(dtype=float)
    bh = initial * closes / closes[0]
    years = max(len(equity) / ppy, 1e-9)
    oos_annual = (equity[-1] / initial) ** (1 / years) - 1
    avg_is = float(np.mean(is_annual)) if is_annual else 0.0
    wins = [t for t in oos_trades if t.pnl > 0]

    step = max(1, len(equity) // 600)
    curve = [{"t": idx[i].isoformat(), "robo": round(float(equity[i]), 2), "buy_hold": round(float(bh[i]), 2)}
             for i in range(0, len(equity), step)]

    metrics = {
        "janelas": len(windows),
        "janelas_positivas": sum(w["retorno_teste"] > 0 for w in windows),
        "janelas_em_caixa": sum(w["acao"] == "caixa" for w in windows),
        "capital_final": round(float(equity[-1]), 2),
        "retorno_total": float(equity[-1] / initial - 1),
        "retorno_anual": float(oos_annual),
        "max_drawdown": max_drawdown(equity),
        "operacoes": len(oos_trades),
        "taxa_acerto": len(wins) / len(oos_trades) if oos_trades else 0.0,
        "buy_hold_retorno": float(bh[-1] / initial - 1),
        "buy_hold_drawdown": max_drawdown(bh),
        "retorno_anual_treino_medio": avg_is,
        # Eficiência: quanto do desempenho do treino sobreviveu fora da amostra
        "eficiencia": float(oos_annual / avg_is) if avg_is > 0 else None,
        "combinacoes_testadas": len(combos),
        "parametros_mais_escolhidos": chosen.most_common(5),
    }
    metrics["veredito"] = _verdict(metrics)
    mc = monte_carlo([t.ret_equity for t in oos_trades], initial, original_dd=metrics["max_drawdown"])
    return {"metricas": metrics, "janelas": windows, "curva": curve,
            "operacoes": [asdict(t) for t in oos_trades], "monte_carlo": mc}


def _verdict(m: dict) -> str:
    eff = m["eficiencia"]
    pos = m["janelas_positivas"] / max(m["janelas"], 1)
    if m["retorno_total"] <= 0:
        return ("Fora da amostra a estratégia perdeu dinheiro. Os bons resultados do backtest vinham de ajuste "
                "ao passado. Não use com dinheiro real.")
    if eff is not None and eff < 0.3:
        return (f"Lucrou fora da amostra, mas só manteve {eff:.0%} do desempenho do treino. "
                "Sinal de excesso de otimização. Use com cautela.")
    if pos < 0.5:
        return f"Lucro concentrado em poucas janelas ({pos:.0%} positivas). Resultado depende de poucos momentos."
    return (f"A estratégia se manteve fora da amostra ({pos:.0%} das janelas positivas"
            + (f", eficiência de {eff:.0%}" if eff is not None else "") + "). Bom sinal. Siga para a simulação.")
