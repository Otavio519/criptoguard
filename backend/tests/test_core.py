from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.backtest import run_backtest
from app.bot import TradingBot
from app.portfolio import Portfolio
from app.broker import PaperBroker
from app.config import StrategyParams
from app.data import synthetic_ohlcv
from app.db import Store
from app.montecarlo import monte_carlo
from app.risk import MonthlyGuard, position_size, stop_price
from app.strategy import MEANREV, TREND, Decider, add_indicators
from app.walkforward import walk_forward

# modo clássico, períodos curtos (para testes pequenos)
P = StrategyParams(sma_fast=3, sma_slow=6, atr_period=3, atr_stop_mult=2, risk_per_trade=0.01,
                   max_monthly_loss=0.05, fee_rate=0.001, use_regime=False)


def frame(closes, spread=0.01, start="2024-01-01"):
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.date_range(start, periods=len(c), freq="D", tz="UTC", name="timestamp")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + spread),
                         "low": np.minimum(o, c) * (1 - spread), "close": c, "volume": 1.0}, index=idx)


# ---------------- risco ----------------
def test_tamanho_da_posicao_respeita_risco():
    q = position_size(10_000, 100.0, 90.0, P)
    assert q * 10 == pytest.approx(100.0)  # 1% de 10 mil
    q2 = position_size(10_000, 100, 99.99, P)
    assert q2 * 100 * (1 + P.fee_rate) <= 10_000 + 1e-6


def test_stop_nunca_abaixo_de_metade():
    assert stop_price(100, 1000, P) == 50


def test_trava_mensal():
    g = MonthlyGuard(0.05)
    assert not g.update("2024-01", 1000)
    assert not g.update("2024-01", 960)
    assert g.update("2024-01", 949)
    assert not g.update("2024-02", 949)


# ---------------- regime ----------------
def test_regime_detecta_alta_baixa_e_lateral():
    p = StrategyParams(regime_sma=50)
    rng = np.random.default_rng(0)
    up = frame(100 * np.exp(np.cumsum(0.01 + rng.normal(0, 0.005, 300))))
    down = frame(100 * np.exp(np.cumsum(-0.01 + rng.normal(0, 0.005, 300))))
    side = frame(100 + rng.normal(0, 1, 300))  # ruído sem direção
    assert add_indicators(up, p)["regime"].iloc[-50:].eq("alta").mean() > 0.9
    assert add_indicators(down, p)["regime"].iloc[-50:].eq("baixa").mean() > 0.9
    assert add_indicators(side, p)["regime"].iloc[-50:].eq("lateral").mean() > 0.7


def test_decider_nao_recompra_logo_apos_stop():
    d = Decider()
    args = dict(ready=True, trend_ok=True, regime="alta", entry_trend=True, exit_trend=False,
                entry_mr=False, exit_mr=False)
    assert d.on_bar(**args, pos_tag=None) == f"buy:{TREND}"
    d.on_stop(TREND)
    assert d.on_bar(**args, pos_tag=None) is None           # bloqueado
    d.on_bar(**{**args, "trend_ok": False, "entry_trend": False}, pos_tag=None)  # tendência reinicia
    assert d.on_bar(**args, pos_tag=None) == f"buy:{TREND}"


def test_decider_reversao_so_no_lateral_e_sai_na_media():
    d = Decider()
    base = dict(ready=True, trend_ok=False, regime="lateral", entry_trend=False, exit_trend=True)
    assert d.on_bar(**base, entry_mr=True, exit_mr=False, pos_tag=None) == f"buy:{MEANREV}"
    assert d.on_bar(**base, entry_mr=False, exit_mr=False, pos_tag=MEANREV) is None
    assert d.on_bar(**base, entry_mr=False, exit_mr=True, pos_tag=MEANREV) == "sell"


# ---------------- backtest ----------------
@pytest.mark.parametrize("use_regime", [False, True])
def test_backtest_sem_olhar_o_futuro(use_regime):
    df = synthetic_ohlcv(days=1500, seed=7)
    p = StrategyParams(use_regime=use_regime)
    r = run_backtest(df, p, 1000)
    ind = add_indicators(df, p)
    assert r["operacoes"]
    for t in r["operacoes"]:
        pos = df.index.get_loc(pd.Timestamp(t["entry_time"]))
        prev = ind.iloc[pos - 1]
        assert prev["ready"]
        assert prev["entry_trend"] if t["strategy"] == TREND else prev["entry_mr"]
        assert t["entry_price"] == pytest.approx(df["open"].iloc[pos])
        if use_regime:
            assert t["regime"] != "baixa"                     # nunca compra em regime de baixa
            if t["strategy"] == MEANREV:
                assert t["regime"] == "lateral"
        if t["reason"] == "stop loss":
            assert t["exit_price"] <= t["stop"] + 1e-9
    soma = sum(t["pnl"] for t in r["operacoes"])
    assert r["metricas"]["capital_final"] == pytest.approx(1000 + soma, abs=0.02)


def test_backtest_resultado_nao_muda_com_dados_futuros():
    df = synthetic_ohlcv(days=1500, seed=11)
    corte = 1000
    alterado = df.copy()
    alterado.iloc[corte:, :4] *= 3  # muda todo o futuro
    a = run_backtest(df.iloc[:corte], StrategyParams(), 1000)["operacoes"]
    b = [t for t in run_backtest(alterado, StrategyParams(), 1000)["operacoes"]
         if pd.Timestamp(t["exit_time"]) < df.index[corte - 1]]
    assert [x["entry_time"] for x in a if pd.Timestamp(x["exit_time"]) < df.index[corte - 1]] == \
           [x["entry_time"] for x in b]


# ---------------- walk-forward ----------------
def test_walkforward_janelas_contiguas_e_sem_vazamento():
    df = synthetic_ohlcv(days=1400, seed=5)
    grid = {"sma_fast": [10, 20], "sma_slow": [50], "atr_stop_mult": [2.0], "adx_min": [20]}
    r = walk_forward(df, StrategyParams(), 1000, 300, 100, grid)
    js = r["janelas"]
    assert len(js) >= 3
    for a, b in zip(js, js[1:]):
        assert pd.Timestamp(b["teste_inicio"]) > pd.Timestamp(a["teste_fim"])
        assert pd.Timestamp(a["teste_inicio"]) > pd.Timestamp(a["treino_inicio"])
    # alterar dados DEPOIS da 2a janela de teste não muda a escolha das duas primeiras
    fim2 = df.index.get_loc(pd.Timestamp(js[1]["teste_fim"]))
    alt = df.copy()
    alt.iloc[fim2 + 1:, :4] *= 0.3
    r2 = walk_forward(alt, StrategyParams(), 1000, 300, 100, grid)
    for k in range(2):
        assert r2["janelas"][k]["parametros"] == js[k]["parametros"]
        assert r2["janelas"][k]["retorno_teste"] == pytest.approx(js[k]["retorno_teste"])
    assert "veredito" in r["metricas"]


def test_walkforward_dados_insuficientes():
    with pytest.raises(ValueError):
        walk_forward(synthetic_ohlcv(days=300), StrategyParams(), 1000, 365, 90)


# ---------------- monte carlo ----------------
def test_monte_carlo_basico():
    assert monte_carlo([0.01] * 5)["ok"] is False
    m = monte_carlo([0.01] * 30, 1000)
    assert m["prob_prejuizo"] == 0 and m["drawdown_p95"] == 0
    assert m["retorno_mediano"] == pytest.approx(1.01 ** 30 - 1)
    rets = np.random.default_rng(1).normal(0, 0.02, 50)
    a, b = monte_carlo(rets, seed=3), monte_carlo(rets, seed=3)
    assert a["retorno_p5"] == b["retorno_p5"]                 # reproduzível
    assert a["retorno_p5"] <= a["retorno_mediano"] <= a["retorno_p95"]
    assert a["faixas"][0]["p50"] == 1000


# ---------------- robô com corretora falsa ----------------
class FakeExchange:
    def __init__(self, closes):
        self.closes = list(closes)
        self.now = 0
        self.price_now = closes[-1]

    def parse_timeframe(self, tf):
        return 86400

    def milliseconds(self):
        return self.now

    def fetch_ticker(self, symbol):
        return {"last": self.price_now}

    def fetch_ohlcv(self, symbol, tf, limit=100):
        base = 1_700_000_000_000
        rows = []
        for i, c in enumerate(self.closes):
            o = self.closes[i - 1] if i else c
            rows.append([base + i * 86_400_000, o, max(o, c) * 1.01, min(o, c) * 0.99, c, 1])
        self.now = rows[-1][0] + 1000  # último candle ainda aberto
        return rows[-limit:]


def test_robo_compra_no_cruzamento_e_sai_no_stop():
    store = Store(":memory:")
    closes = [10.0] * 10 + [9, 8, 7]
    ex = FakeExchange(closes + [7])
    broker = PaperBroker(ex, "BTC/USDT", store, 1000, 0.001)
    bot = TradingBot(broker, ex, "BTC/USDT", "1d", P, store)
    pf = Portfolio([bot], store, P.max_monthly_loss)

    pf.tick()
    assert store.get("last_candle_paper_BTCUSDT") is not None and bot.position is None
    assert store.get("market_paper_BTCUSDT")["regime"] == "sem filtro"

    ex.closes = closes + [9, 12, 12]
    ex.price_now = 12
    for _ in range(3):
        pf.tick()
        if bot.position:
            break
        ex.closes.append(ex.closes[-1] + 0.5)
    pos = bot.position
    assert pos is not None and pos["strategy"] == TREND
    assert pos["qty"] * (pos["entry_price"] - pos["stop"]) <= 1000 * 0.01 * 1.01

    ex.price_now = pos["stop"] * 0.98
    pf.tick()
    assert bot.position is None
    assert store.rows("SELECT reason FROM trades WHERE side='venda'")[0]["reason"] == "stop loss"
    assert store.get("decider_paper_BTCUSDT")["rearm"] is False             # não recompra na hora


# ---------------- API ----------------
def test_api():
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/backtest", json={"demo": True})
    assert r.status_code == 200
    body = r.json()
    assert body["monte_carlo"]["ok"] and len(body["curva"]) > 10
    assert "regimes" in body["metricas"]
    assert c.post("/api/backtest", json={"demo": True, "sma_fast": 50, "sma_slow": 20}).status_code == 400
    w = c.post("/api/walkforward", json={"demo": True, "grid": {"sma_fast": [10, 20], "sma_slow": [50],
                                                               "atr_stop_mult": [2], "adx_min": [20]}})
    assert w.status_code == 200, w.text
    assert w.json()["metricas"]["janelas"] > 3
    assert c.get("/api/health").json()["ok"]
    assert c.get("/api/bot/status").json()["mode"] == "paper"


def test_api_senha(monkeypatch):
    from app import main
    monkeypatch.setattr(main.settings, "panel_password", "segredo")
    monkeypatch.setattr(main.settings, "panel_user", "antonio")
    c = TestClient(main.app)
    assert c.get("/api/config").status_code == 401
    assert c.get("/api/config", auth=("antonio", "errada")).status_code == 401
    assert c.get("/api/config", auth=("antonio", "segredo")).status_code == 200
    assert c.get("/api/health").status_code == 200            # healthcheck do Docker sem senha


def test_api_iniciar_e_parar_robo():
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/bot/start").status_code == 200
        assert c.get("/api/health").json()["bot_running"] is True
        assert c.post("/api/bot/stop").status_code == 200
        assert c.get("/api/health").json()["bot_running"] is False


def test_stop_movel_so_sobe_e_sai_pelo_stop():
    df = synthetic_ohlcv(days=1500, seed=7)
    p = StrategyParams(trail_atr_mult=3.0)
    r = run_backtest(df, p, 1000)
    base = run_backtest(df, StrategyParams(), 1000)
    assert r["operacoes"]
    for t in r["operacoes"]:
        if t["reason"] == "stop loss":
            assert t["exit_price"] <= t["stop"] + 1e-9
    # stop móvel nunca deixa a pior perda ficar maior que a do stop fixo
    assert min(t["pnl"] for t in r["operacoes"]) >= min(t["pnl"] for t in base["operacoes"]) - 5


def test_filtro_volume_e_sem_reversao_reduzem_entradas():
    df = synthetic_ohlcv(days=1500, seed=7)
    todos = add_indicators(df, StrategyParams())
    filtro = add_indicators(df, StrategyParams(vol_filter=1.2, use_meanrev=False))
    assert filtro["entry_trend"].sum() <= todos["entry_trend"].sum()
    assert filtro["entry_mr"].sum() == 0


# ---------------- stop dentro da corretora (modo real / testnet) ----------------
from app.broker import LiveBroker


class FakeLiveExchange(FakeExchange):
    """Corretora falsa com saldo, ordens a mercado e ordens de stop."""

    def __init__(self, closes, order_types=("LIMIT", "MARKET", "STOP_LOSS_LIMIT")):
        super().__init__(closes)
        self.bal = {"USDT": 10000.0, "BTC": 0.0}
        self.locked = 0.0
        self.orders = {}
        self.n = 0
        self.order_types = list(order_types)

    def load_markets(self):
        return {}

    def market(self, symbol):
        return {"base": "BTC", "quote": "USDT", "limits": {"amount": {"min": 0.0}, "cost": {"min": 5}},
                "info": {"orderTypes": self.order_types}}

    def amount_to_precision(self, s, q):
        import math
        return f"{math.floor(q * 1e6) / 1e6:.6f}"  # o ccxt trunca a quantidade, igual aqui

    def price_to_precision(self, s, p):
        return f"{p:.2f}"

    def fetch_balance(self):
        return {"total": dict(self.bal), "free": {"USDT": self.bal["USDT"], "BTC": self.bal["BTC"] - self.locked}}

    def create_market_buy_order(self, s, q):
        px = self.price_now
        self.bal["USDT"] -= q * px
        self.bal["BTC"] += q * 0.999  # taxa cobrada em BTC, como na Binance
        return {"filled": q, "average": px, "fee": {"cost": q * 0.001, "currency": "BTC"}}

    def create_market_sell_order(self, s, q):
        assert q <= self.bal["BTC"] - self.locked + 1e-9, "vendeu moeda travada no stop"
        px = self.price_now
        self.bal["BTC"] -= q
        self.bal["USDT"] += q * px * 0.999
        return {"filled": q, "average": px, "fee": {"cost": q * px * 0.001, "currency": "USDT"}}

    def create_order(self, s, typ, side, q, price, params):
        self.n += 1
        oid = str(self.n)
        self.orders[oid] = {"id": oid, "type": typ, "status": "open", "amount": q, "filled": 0.0,
                            "stopPrice": params["stopPrice"], "price": price}
        self.locked += q
        return self.orders[oid]

    def trigger(self, oid, px):
        o = self.orders[oid]
        o.update(status="closed", filled=o["amount"], average=px, fee={"cost": o["amount"] * px * 0.001, "currency": "USDT"})
        self.locked -= o["amount"]
        self.bal["BTC"] -= o["amount"]
        self.bal["USDT"] += o["amount"] * px * 0.999

    def fetch_order(self, oid, s):
        return self.orders[oid]

    def cancel_order(self, oid, s):
        o = self.orders[oid]
        if o["status"] == "open":
            o["status"] = "canceled"
            self.locked -= o["amount"]


def _bot_comprado(order_types=("LIMIT", "MARKET", "STOP_LOSS_LIMIT")):
    store = Store(":memory:")
    closes = [10.0] * 10 + [9, 8, 7]
    ex = FakeLiveExchange(closes + [7], order_types)
    bot = TradingBot(LiveBroker(ex, "BTC/USDT"), ex, "BTC/USDT", "1d", P, store)
    bot.pf = Portfolio([bot], store, P.max_monthly_loss)
    bot.tick = bot.pf.tick
    bot.tick()
    ex.closes = closes + [9, 12, 12]
    ex.price_now = 12
    for _ in range(3):
        bot.tick()
        if bot.position:
            break
        ex.closes.append(ex.closes[-1] + 0.5)
    assert bot.position is not None
    return bot, ex, store


def test_compra_registra_stop_na_corretora():
    bot, ex, store = _bot_comprado()
    pos = bot.position
    o = ex.orders[pos["stop_order_id"]]
    assert o["type"] == "STOP_LOSS_LIMIT" and o["status"] == "open"
    assert float(o["stopPrice"]) == pytest.approx(pos["stop"], abs=0.01)
    assert float(o["price"]) < float(o["stopPrice"])          # limite abaixo do gatilho
    assert pos["qty"] == pytest.approx(ex.bal["BTC"])            # taxa em BTC descontada da posição


def test_usa_stop_a_mercado_quando_a_corretora_aceita():
    bot, ex, store = _bot_comprado(("LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_LIMIT"))
    assert ex.orders[bot.position["stop_order_id"]]["type"] == "STOP_LOSS"


def test_stop_executado_com_computador_desligado_e_registrado_ao_voltar():
    bot, ex, store = _bot_comprado()
    pos = bot.position
    ex.trigger(pos["stop_order_id"], pos["stop"])   # executou na corretora "de madrugada"
    ex.price_now = pos["stop"] * 0.97
    bot.tick()                                      # computador ligou de novo
    assert bot.position is None
    venda = store.rows("SELECT * FROM trades WHERE side='venda'")[0]
    assert venda["reason"] == "stop na corretora" and venda["pnl"] < 0
    assert store.get("decider_live_BTCUSDT")["rearm"] is False


def test_venda_por_sinal_cancela_o_stop_antes():
    bot, ex, store = _bot_comprado()
    oid = bot.position["stop_order_id"]
    bot.sell(ex.price_now, "sinal de venda")
    assert ex.orders[oid]["status"] == "canceled"
    assert bot.position is None and ex.locked == pytest.approx(0)


def test_moeda_travada_no_stop_nao_dispara_trava_mensal():
    bot, ex, store = _bot_comprado()
    bot.tick()
    bot.tick()
    assert bot.position is not None and not bot.pf.guard.locked
    q, b = bot.broker.balances()
    assert b > 0  # saldo total conta a moeda presa na ordem de stop


def test_stop_recolocado_se_sumir_da_corretora():
    bot, ex, store = _bot_comprado()
    old = bot.position["stop_order_id"]
    ex.cancel_order(old, "BTC/USDT")               # alguém cancelou no app
    bot.tick()
    novo = bot.position["stop_order_id"]
    assert novo and novo != old and ex.orders[novo]["status"] == "open"


# ---------------- carteira com várias moedas ----------------
class FakeMulti:
    """Várias moedas com preços independentes (simulação)."""

    def __init__(self, symbols):
        self.m = {s: FakeExchange([10.0] * 10 + [9, 8, 7, 7]) for s in symbols}
        self.fail = set()
        self.now = 0

    def parse_timeframe(self, tf):
        return 86400

    def milliseconds(self):
        return max(e.now for e in self.m.values())

    def fetch_ticker(self, s):
        return self.m[s].fetch_ticker(s)

    def fetch_ohlcv(self, s, tf, limit=100):
        if s in self.fail:
            raise ConnectionError("corretora fora do ar")
        return self.m[s].fetch_ohlcv(s, tf, limit)

    def pump(self, s):
        e = self.m[s]
        e.closes = e.closes[:13] + [9, 12, 12]
        e.price_now = 12

    def step(self, s):
        e = self.m[s]
        e.closes.append(e.closes[-1] + 0.5)


def _ate_comprar(pf, ex, symbols):
    for _ in range(4):
        pf.tick()
        pend = [s for s in symbols if not next(b for b in pf.bots if b.symbol == s).position]
        if not pend:
            return
        for s in pend:
            ex.step(s)


def _carteira(symbols=("BTC/USDT", "ETH/USDT"), start=1000):
    store = Store(":memory:")
    ex = FakeMulti(symbols)
    bots = [TradingBot(PaperBroker(ex, s, store, start, 0.001), ex, s, "1d", P, store) for s in symbols]
    return Portfolio(bots, store, P.max_monthly_loss), ex, store


def test_carteira_divide_o_risco_entre_as_moedas():
    pf, ex, store = _carteira()
    pf.tick()
    for s in ex.m:
        ex.pump(s)
    _ate_comprar(pf, ex, list(ex.m))
    btc, eth = pf.bots
    assert btc.position and eth.position
    wallet = store.get("paper_wallet")
    assert wallet["BTC"] > 0 and wallet["ETH"] > 0
    for b in pf.bots:
        risco = b.position["qty"] * (b.position["entry_price"] - b.position["stop"])
        assert risco <= 1000 / 2 * 0.01 * 1.05       # 1% da fatia de cada moeda (metade do capital)
    w = store.get("wallet_paper")
    px = w["prices"]
    assert w["equity"] == pytest.approx(w["quote"] + w["assets"]["BTC"] * px["BTC/USDT"] + w["assets"]["ETH"] * px["ETH/USDT"])
    assert {t["symbol"] for t in store.rows("SELECT symbol FROM trades")} == {"BTC/USDT", "ETH/USDT"}


def test_trava_mensal_da_carteira_vende_todas_as_moedas():
    pf, ex, store = _carteira()
    pf.tick()
    for s in ex.m:
        ex.pump(s)
    _ate_comprar(pf, ex, list(ex.m))
    assert all(b.position for b in pf.bots)
    pf.guard.start_equity = store.get("wallet_paper")["equity"] * 1.10  # carteira "perdeu" 10% no mês
    pf.tick()
    assert pf.guard.locked and not any(b.position for b in pf.bots)
    motivos = {r["reason"] for r in store.rows("SELECT reason FROM trades WHERE side='venda'")}
    assert motivos == {"trava de perda mensal"}


def test_erro_em_uma_moeda_nao_para_as_outras():
    pf, ex, store = _carteira()
    pf.tick()
    ex.fail.add("ETH/USDT")
    ex.pump("BTC/USDT")
    for _ in range(4):
        try:
            pf.tick()
        except RuntimeError as e:
            assert "ETH/USDT" in str(e)
        if pf.bots[0].position:
            break
        ex.step("BTC/USDT")
    btc, eth = pf.bots
    assert btc.position is not None and eth.position is None
    assert "fora do ar" in eth.last_error


def test_symbols_valida_moeda_de_cotacao(monkeypatch):
    from app.config import Settings
    s = Settings()
    monkeypatch.setattr(s, "symbols_raw", "btc/usdt, eth/usdt ,BTC/USDT")
    assert s.symbols == ["BTC/USDT", "ETH/USDT"]
    monkeypatch.setattr(s, "symbols_raw", "BTC/USDT,ETH/BRL")
    with pytest.raises(ValueError):
        _ = s.symbols


def test_stop_movel_sobe_a_ordem_na_corretora():
    p = replace(P, trail_atr_mult=2.0)
    store = Store(":memory:")
    closes = [10.0] * 10 + [9, 8, 7]
    ex = FakeLiveExchange(closes + [7])
    bot = TradingBot(LiveBroker(ex, "BTC/USDT"), ex, "BTC/USDT", "1d", p, store)
    pf = Portfolio([bot], store, p.max_monthly_loss)
    pf.tick()
    ex.closes = closes + [9, 12, 12]
    ex.price_now = 12
    for _ in range(3):
        pf.tick()
        if bot.position:
            break
        ex.closes.append(ex.closes[-1] + 0.5)
    first = bot.position["stop_order_id"]
    stop0 = bot.position["stop"]
    ex.closes += [14, 16, 18]            # preço sobe forte
    ex.price_now = 18
    pf.tick()
    pos = bot.position
    assert pos["stop"] > stop0
    assert ex.orders[first]["status"] == "canceled"
    novo = ex.orders[pos["stop_order_id"]]
    assert novo["status"] == "open" and float(novo["stopPrice"]) == pytest.approx(pos["stop"], abs=0.01)


def test_migra_estado_antigo_de_uma_moeda(monkeypatch):
    from app import main
    main.store.set("position_paper", {"qty": 1, "stop": 5, "entry_price": 10, "cost": 10})
    main._migrate_single_symbol_state("paper", "BTC/USDT")
    assert main.store.get("position_paper_BTCUSDT")["qty"] == 1
    assert main.store.get("position_paper") is None
    main.store.set("position_paper_BTCUSDT", None)
