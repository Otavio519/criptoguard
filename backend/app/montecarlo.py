"""Simulação Monte Carlo.

Pega o resultado de cada operação (em % do patrimônio) e sorteia novas sequências com reposição,
milhares de vezes. Responde perguntas como:
- Qual a pior queda que eu devo esperar em 95% dos casos?
- Qual a chance de terminar no prejuízo?
- A sequência do backtest foi sorte?
"""
from __future__ import annotations

import numpy as np


def monte_carlo(trade_returns, initial: float = 1000.0, n_sims: int = 5000, seed: int = 7,
                dd_limit: float = 0.20, ruin: float = 0.50, original_dd: float | None = None) -> dict:
    r = np.asarray(trade_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 10:
        return {"ok": False, "motivo": f"Só {len(r)} operações. O Monte Carlo precisa de pelo menos 10 "
                                         "para dar uma resposta confiável. Use um período maior."}
    rng = np.random.default_rng(seed)
    samples = rng.choice(r, size=(n_sims, len(r)), replace=True)
    growth = np.cumprod(1 + np.clip(samples, -0.99, None), axis=1)
    paths = np.hstack([np.ones((n_sims, 1)), growth]) * initial
    final = paths[:, -1]
    peaks = np.maximum.accumulate(paths, axis=1)
    dd = ((paths - peaks) / peaks).min(axis=1)
    final_ret = final / initial - 1

    q = lambda a, x: float(np.percentile(a, x))  # noqa: E731
    steps = paths.shape[1]
    idx = np.unique(np.linspace(0, steps - 1, min(steps, 150)).astype(int))
    bands = np.percentile(paths[:, idx], [5, 25, 50, 75, 95], axis=0)
    fan = [{"n": int(i), "p5": round(float(bands[0, k]), 2), "p25": round(float(bands[1, k]), 2),
            "p50": round(float(bands[2, k]), 2), "p75": round(float(bands[3, k]), 2),
            "p95": round(float(bands[4, k]), 2)} for k, i in enumerate(idx)]
    counts, edges = np.histogram(final_ret, bins=30)
    hist = [{"de": float(edges[k]), "ate": float(edges[k + 1]), "n": int(counts[k])} for k in range(len(counts))]

    dd95 = q(dd, 5)  # 5% piores caminhos
    res = {
        "ok": True,
        "simulacoes": n_sims,
        "operacoes_por_simulacao": int(len(r)),
        "retorno_p5": q(final_ret, 5),
        "retorno_mediano": q(final_ret, 50),
        "retorno_p95": q(final_ret, 95),
        "drawdown_mediano": q(dd, 50),
        "drawdown_p95": dd95,
        "prob_prejuizo": float((final < initial).mean()),
        "prob_drawdown_limite": float((dd <= -dd_limit).mean()),
        "drawdown_limite": dd_limit,
        "prob_ruina": float((paths.min(axis=1) <= initial * (1 - ruin)).mean()),
        "ruina": ruin,
        "faixas": fan,
        "histograma": hist,
    }
    if original_dd is not None:
        res["drawdown_backtest"] = original_dd
        worse = float((dd < original_dd).mean())
        res["simulacoes_com_queda_pior"] = worse
        # se mais de 65% dos caminhos tiveram queda pior, a ordem das operações do backtest foi favorável
        res["backtest_teve_sorte"] = worse > 0.65
    res["veredito"] = _verdict(res)
    return res


def _verdict(m: dict) -> str:
    partes = []
    if m["prob_prejuizo"] > 0.4:
        partes.append(f"Em {m['prob_prejuizo']:.0%} das simulações você termina no prejuízo. Estratégia fraca.")
    elif m["prob_prejuizo"] > 0.15:
        partes.append(f"Chance de prejuízo de {m['prob_prejuizo']:.0%}. Resultado positivo, mas instável.")
    else:
        partes.append(f"Só {m['prob_prejuizo']:.0%} das simulações terminam no prejuízo. Boa consistência.")
    partes.append(f"Prepare o bolso para uma queda de até {abs(m['drawdown_p95']):.0%} (95% dos casos).")
    if m.get("backtest_teve_sorte"):
        partes.append(f"A queda do backtest ({abs(m['drawdown_backtest']):.0%}) foi menor que em "
                      f"{m['simulacoes_com_queda_pior']:.0%} das simulações. A ordem das operações ajudou. "
                      "Não conte com essa sorte.")
    if m["prob_ruina"] > 0.01:
        partes.append(f"Risco de perder metade do capital: {m['prob_ruina']:.1%}. Reduza o risco por operação.")
    return " ".join(partes)
