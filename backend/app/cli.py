"""Backtest e walk-forward pelo terminal.

Exemplos:
  python -m app.cli --since 2020-01-01
  python -m app.cli --symbol ETH/USDT --fast 10 --slow 30
  python -m app.cli --sem-regime           (modo clássico, só cruzamento de médias)
  python -m app.cli --walkforward          (validação fora da amostra)
  python -m app.cli --demo                 (dados sintéticos, sem internet)
"""
import argparse
from dataclasses import replace

from .backtest import run_backtest
from .config import settings
from .data import load_or_fetch, synthetic_ohlcv
from .montecarlo import monte_carlo
from .walkforward import walk_forward


def pct(v):
    return f"{v * 100:+.1f}%"


def print_mc(mc):
    print("\n--- Monte Carlo (5.000 simulações) ---")
    if not mc["ok"]:
        print(mc["motivo"])
        return
    print(f"Retorno: pior 5% {pct(mc['retorno_p5'])} | mediano {pct(mc['retorno_mediano'])} | "
          f"melhor 5% {pct(mc['retorno_p95'])}")
    print(f"Queda máxima em 95% dos casos: {pct(mc['drawdown_p95'])}")
    print(f"Chance de prejuízo: {mc['prob_prejuizo']:.0%}")
    print(mc["veredito"])


def main():
    a = argparse.ArgumentParser(description="CriptoGuard pelo terminal")
    a.add_argument("--symbol", default=settings.symbol)
    a.add_argument("--timeframe", default=settings.timeframe)
    a.add_argument("--since", default="2020-01-01")
    a.add_argument("--until", default=None)
    a.add_argument("--initial", type=float, default=1000)
    a.add_argument("--fast", type=int, default=settings.params.sma_fast)
    a.add_argument("--slow", type=int, default=settings.params.sma_slow)
    a.add_argument("--risk", type=float, default=settings.params.risk_per_trade)
    a.add_argument("--sem-regime", action="store_true", help="desliga o filtro de regime")
    a.add_argument("--walkforward", action="store_true", help="roda a validação walk-forward")
    a.add_argument("--treino", type=int, default=365, help="candles de treino no walk-forward")
    a.add_argument("--teste", type=int, default=90, help="candles de teste no walk-forward")
    a.add_argument("--demo", action="store_true", help="usa dados sintéticos, sem internet")
    args = a.parse_args()

    p = replace(settings.params, sma_fast=args.fast, sma_slow=args.slow, risk_per_trade=args.risk,
                use_regime=not args.sem_regime)
    df = synthetic_ohlcv(days=2200) if args.demo else load_or_fetch(
        settings.exchange, args.symbol, args.timeframe, args.since, args.until)
    tag = f"{args.symbol} {args.timeframe} | {len(df)} candles | regime {'ligado' if p.use_regime else 'desligado'}"

    if args.walkforward:
        r = walk_forward(df, p, args.initial, args.treino, args.teste)
        m = r["metricas"]
        print(f"\n=== Walk-forward {tag} ===")
        print(f"{m['combinacoes_testadas']} combinações testadas em cada uma de {m['janelas']} janelas")
        print(f"Resultado fora da amostra ... {m['capital_final']:.2f} ({pct(m['retorno_total'])})")
        print(f"Retorno ao ano .............. {pct(m['retorno_anual'])}")
        print(f"Pior queda .................. {pct(m['max_drawdown'])}")
        print(f"Janelas positivas ........... {m['janelas_positivas']} de {m['janelas']}")
        if m["eficiencia"] is not None:
            print(f"Eficiência .................. {m['eficiencia']:.0%}")
        print(f"Comprar e segurar ........... {pct(m['buy_hold_retorno'])}")
        print(m["veredito"])
        print_mc(r["monte_carlo"])
        return

    r = run_backtest(df, p, args.initial)
    m = r["metricas"]
    print(f"\n=== Backtest {tag} ===")
    print(f"Capital final ....... {m['capital_final']:.2f} ({pct(m['retorno_total'])})")
    print(f"Retorno ao ano ...... {pct(m['retorno_anual'])}")
    print(f"Pior queda .......... {pct(m['max_drawdown'])}")
    print(f"Operações ........... {m['operacoes']} | acerto {m['taxa_acerto'] * 100:.0f}%")
    for nome, s in m["por_estrategia"].items():
        print(f"  {nome:<12} {s['operacoes']} ops | resultado {s['resultado']:+.2f}")
    print(f"Tempo no mercado .... {m['tempo_no_mercado']:.0%}")
    print(f"Comprar e segurar ... {pct(m['buy_hold_retorno'])} (pior queda {pct(m['buy_hold_drawdown'])})")
    print_mc(monte_carlo([t["ret_equity"] for t in r["operacoes"]], args.initial,
                         original_dd=m["max_drawdown"]))


if __name__ == "__main__":
    main()
