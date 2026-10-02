"""API do CriptoGuard. Rode com: uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import base64
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .backtest import run_backtest
from .bot import TradingBot, slug
from .broker import LiveBroker, PaperBroker
from .config import LIVE_PHRASE, StrategyParams, settings
from .data import load_or_fetch, make_exchange, synthetic_ohlcv
from .db import Store
from .portfolio import Portfolio
from .montecarlo import monte_carlo
from .walkforward import DEFAULT_GRID, walk_forward

store = Store(settings.db_path)
_bot: Portfolio | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("REQUIRE_PASSWORD", "false").lower() == "true" and not settings.panel_password:
        raise RuntimeError("Servidor sem senha. Defina PANEL_USER e PANEL_PASSWORD no .env antes de subir.")
    # Depois de reiniciar (queda de energia, atualização, container reiniciado),
    # o robô volta sozinho se estava rodando antes ou se AUTO_START=true.
    if settings.auto_start or store.get("should_run"):
        try:
            get_bot().start()
            store.log("Robô retomado automaticamente após reinício.")
        except Exception as e:  # noqa: BLE001
            store.log(f"Não consegui retomar o robô: {e}", "error")
    yield
    if _bot:
        await _bot.stop()


app = FastAPI(title="CriptoGuard", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"],
                   allow_headers=["*"])


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Senha no painel. Obrigatória quando o sistema roda num servidor exposto na internet."""
    if not settings.panel_password or request.url.path == "/api/health":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    ok = False
    if header.startswith("Basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
            ok = secrets.compare_digest(user, settings.panel_user or "admin") and \
                secrets.compare_digest(pwd, settings.panel_password)
        except (ValueError, UnicodeDecodeError):
            ok = False
    if ok:
        return await call_next(request)
    return Response("Acesso negado", 401, {"WWW-Authenticate": 'Basic realm="CriptoGuard"'})


def _migrate_single_symbol_state(mode: str, symbol: str) -> None:
    """Versões antigas guardavam o estado sem o nome da moeda. Passa para o formato novo."""
    for name in ("position", "decider", "last_candle"):
        old = store.get(f"{name}_{mode}")
        new_key = f"{name}_{mode}_{slug(symbol)}"
        if old is not None and store.get(new_key) is None:
            store.set(new_key, old)
        if old is not None:
            store.set(f"{name}_{mode}", None)


def get_bot():
    global _bot
    if _bot is None and settings.strategy == "rotacao":
        from .rotacao import RotacaoPortfolio, RotParams
        data_ex = make_exchange(settings.exchange)
        if settings.mode == "live":
            if not settings.live_allowed:
                raise HTTPException(400, "Modo live bloqueado. Confira API_KEY, API_SECRET e a frase "
                                         f"LIVE_CONFIRM=\"{LIVE_PHRASE}\" no .env.")
            trade_ex = make_exchange(settings.exchange, settings.api_key, settings.api_secret, settings.use_testnet)
            brokers = [LiveBroker(trade_ex, s) for s in settings.symbols]
        else:
            brokers = [PaperBroker(data_ex, s, store, settings.paper_start_balance, settings.params.fee_rate)
                       for s in settings.symbols]
        p = RotParams(look=settings.rot_look, topk=settings.rot_topk, sma=settings.rot_sma, alvo_vol=settings.rot_vol,
                      rebal_dias=settings.rot_rebal, stop=settings.rot_stop, trava=settings.rot_trava,
                      capital=settings.rot_capital if settings.mode == "live" else settings.paper_start_balance)
        if store.get(f"estrategia_{settings.mode}") != "rotacao":
            # troca de estratégia: o gráfico de patrimônio começa do zero (o capital do robô é outro)
            with store.lock, store.conn:
                store.conn.execute("DELETE FROM equity WHERE mode=?", (settings.mode,))
            store.set(f"estrategia_{settings.mode}", "rotacao")
            store.log(f"Estratégia trocada para Rotação inteligente. Capital do robô: {p.capital:.2f} USDT.")
        teste = settings.mode == "live" and settings.use_testnet
        if teste:
            for b in brokers:
                b.exchange_stop = False  # testnet: o robô vigia o stop com o preço do mercado real
        _bot = RotacaoPortfolio(brokers, data_ex, store, p, stop_preco_real=teste)
    if _bot is None:
        symbols = settings.symbols
        data_ex = make_exchange(settings.exchange)
        if settings.mode == "live":
            if not settings.live_allowed:
                raise HTTPException(400, "Modo live bloqueado. Confira API_KEY, API_SECRET e a frase "
                                         f"LIVE_CONFIRM=\"{LIVE_PHRASE}\" no .env.")
            trade_ex = make_exchange(settings.exchange, settings.api_key, settings.api_secret, settings.use_testnet)
            brokers = [LiveBroker(trade_ex, s) for s in symbols]
            # candles sempre da corretora real (a testnet tem histórico curto demais para a média de 200)
        else:
            brokers = [PaperBroker(data_ex, s, store, settings.paper_start_balance, settings.params.fee_rate)
                       for s in symbols]
        _migrate_single_symbol_state(settings.mode, settings.symbol if settings.symbol in symbols else symbols[0])
        bots = [TradingBot(b, data_ex, s, settings.timeframe, settings.params, store)
                for b, s in zip(brokers, symbols)]
        _bot = Portfolio(bots, store, settings.params.max_monthly_loss)
    return _bot


# ---------------- Backtest / Walk-forward ----------------
_p = settings.params


class ParamsIn(BaseModel):
    symbol: str = settings.symbol
    timeframe: str = settings.timeframe
    since: str = "2020-01-01"
    until: str | None = None
    initial: float = Field(1000, gt=0)
    sma_fast: int = _p.sma_fast
    sma_slow: int = _p.sma_slow
    atr_period: int = _p.atr_period
    atr_stop_mult: float = _p.atr_stop_mult
    risk_per_trade: float = _p.risk_per_trade
    max_monthly_loss: float = _p.max_monthly_loss
    fee_rate: float = _p.fee_rate
    use_regime: bool = _p.use_regime
    regime_sma: int = _p.regime_sma
    adx_period: int = _p.adx_period
    adx_min: float = _p.adx_min
    bb_period: int = _p.bb_period
    bb_std: float = _p.bb_std
    rsi_period: int = _p.rsi_period
    rsi_buy: float = _p.rsi_buy
    trail_atr_mult: float = _p.trail_atr_mult
    vol_filter: float = _p.vol_filter
    use_meanrev: bool = _p.use_meanrev
    demo: bool = False  # True = dados sintéticos, funciona sem internet

    def params(self) -> StrategyParams:
        return StrategyParams(**{k: getattr(self, k) for k in StrategyParams.__dataclass_fields__})


class WalkForwardIn(ParamsIn):
    train_bars: int = Field(365, ge=60, le=3000)
    test_bars: int = Field(90, ge=10, le=1000)
    grid: dict[str, list[float]] | None = None


async def _load(body: ParamsIn):
    if body.demo:
        return synthetic_ohlcv(days=2200)
    return await asyncio.to_thread(load_or_fetch, settings.exchange, body.symbol, body.timeframe,
                                   body.since, body.until)


def _source(body: ParamsIn) -> str:
    return "dados sintéticos (demo)" if body.demo else f"{settings.exchange} {body.symbol} {body.timeframe}"


async def _guard(coro):
    try:
        return await coro
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Falha ao buscar dados na corretora: {e}") from e


@app.post("/api/backtest")
async def backtest(body: ParamsIn):
    async def run():
        df = await _load(body)
        result = await asyncio.to_thread(run_backtest, df, body.params(), body.initial)
        rets = [t["ret_equity"] for t in result["operacoes"]]
        result["monte_carlo"] = await asyncio.to_thread(
            monte_carlo, rets, body.initial, original_dd=result["metricas"]["max_drawdown"])
        result["fonte"] = _source(body)
        return result
    return await _guard(run())


@app.post("/api/walkforward")
async def walkforward(body: WalkForwardIn):
    async def run():
        df = await _load(body)
        grid = body.grid
        if grid:
            ints = {"sma_fast", "sma_slow", "atr_period", "regime_sma", "adx_period", "bb_period", "rsi_period"}
            grid = {k: [int(v) if k in ints else v for v in vals] for k, vals in grid.items()
                    if k in StrategyParams.__dataclass_fields__}
        result = await asyncio.to_thread(walk_forward, df, body.params(), body.initial, body.train_bars,
                                         body.test_bars, grid)
        result["fonte"] = _source(body)
        return result
    return await _guard(run())


@app.get("/api/walkforward/grid")
def default_grid():
    return DEFAULT_GRID


# ---------------- Robô ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "bot_running": _bot.running if _bot else False}


@app.get("/api/config")
def config():
    return {"exchange": settings.exchange, "symbol": settings.symbols[0], "symbols": settings.symbols,
            "timeframe": settings.timeframe,
            "mode": settings.mode, "live_allowed": settings.live_allowed, "testnet": settings.use_testnet,
            "strategy": settings.strategy,
            "rot": {"look": settings.rot_look, "topk": settings.rot_topk, "sma": settings.rot_sma, "vol": settings.rot_vol,
                    "rebal_dias": settings.rot_rebal, "stop": settings.rot_stop, "trava": settings.rot_trava,
                    "capital": settings.rot_capital},
            "params": settings.params.__dict__}


@app.get("/api/bot/status")
def status():
    mode = settings.mode
    symbols = settings.symbols
    wallet = store.get(f"wallet_{mode}")
    if wallet is None and mode == "paper":
        w = store.get("paper_wallet") or {"USDT": settings.paper_start_balance}
        quote = symbols[0].split("/")[1]
        wallet = {"quote": w.get(quote, settings.paper_start_balance),
                  "assets": {k: v for k, v in w.items() if k != quote}, "equity": None, "prices": {}}
    coins = []
    for sym in symbols:
        k = lambda name: f"{name}_{mode}_{slug(sym)}"  # noqa: E731
        bot = next((b for b in _bot.bots if b.symbol == sym), None) if _bot else None
        erro = bot.last_error if bot else (getattr(_bot, "erros", {}).get(sym, "") if _bot else "")
        coins.append({"symbol": sym, "position": store.get(k("position")), "market": store.get(k("market")),
                      "last_error": erro})
    return {
        "running": _bot.running if _bot else False,
        "mode": mode,
        "testnet": settings.use_testnet,
        "timeframe": "1d" if settings.strategy == "rotacao" else settings.timeframe,
        "strategy": settings.strategy,
        "symbols": symbols,
        "coins": coins,
        "wallet": wallet,
        "guard": store.get(f"guard_{mode}"),
        "last_error": _bot.last_error if _bot else "",
        "last_tick": _bot.last_tick if _bot else "",
    }


@app.post("/api/bot/start")
async def start():
    get_bot().start()
    store.set("should_run", True)
    return {"ok": True}


@app.post("/api/bot/stop")
async def stop():
    store.set("should_run", False)
    if _bot:
        await _bot.stop()
    return {"ok": True}


@app.post("/api/bot/panic")
async def panic():
    store.set("should_run", False)
    await get_bot().panic()
    return {"ok": True}


@app.post("/api/bot/reset-paper")
async def reset_paper():
    global _bot
    if settings.mode != "paper":
        raise HTTPException(400, "Só dá para zerar a simulação no modo paper.")
    if _bot and _bot.running:
        raise HTTPException(400, "Pare o robô antes de zerar.")
    store.delete_like("%\\_paper%")  # posição, stop, decisões e trava de todas as moedas
    for k in ("paper_balance", "paper_wallet"):
        store.set(k, None)
    store.set("paper_wallet", {settings.symbols[0].split("/")[1]: settings.paper_start_balance})
    with store.lock, store.conn:
        store.conn.execute("DELETE FROM trades WHERE mode='paper'")
        store.conn.execute("DELETE FROM equity WHERE mode='paper'")
    _bot = None
    store.log("Simulação zerada.")
    return {"ok": True}


@app.get("/api/bot/trades")
def trades():
    return store.rows("SELECT * FROM trades WHERE mode=? ORDER BY id DESC LIMIT 200", (settings.mode,))


@app.get("/api/bot/equity")
def equity():
    rows = store.rows("SELECT time, value FROM equity WHERE mode=? ORDER BY time", (settings.mode,))
    step = max(1, len(rows) // 500)
    return rows[::step]


@app.get("/api/bot/logs")
def logs():
    return store.rows("SELECT * FROM logs ORDER BY rowid DESC LIMIT 100")



# ---------------- Chaves da corretora pelo navegador (servidor) ----------------
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ChavesIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=128)
    api_secret: str = Field(min_length=20, max_length=128)


def _gravar_env(valores: dict[str, str]) -> None:
    import re
    s = ENV_FILE.read_text(encoding="utf-8-sig") if ENV_FILE.exists() else ""
    for k, v in valores.items():
        if re.search(rf"(?m)^{k}=.*$", s):
            s = re.sub(rf"(?m)^{k}=.*$", lambda _m, k=k, v=v: f"{k}={v}", s)
        else:
            s += f"\n{k}={v}\n"
    ENV_FILE.write_text(s, encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


@app.post("/api/config/chaves")
async def salvar_chaves(body: ChavesIn):
    """Recebe as chaves da TESTNET, testa a conexão e só então grava no .env e reinicia o sistema."""
    import re
    key, sec = re.sub(r"\s+", "", body.api_key), re.sub(r"\s+", "", body.api_secret)
    if key == sec:
        raise HTTPException(400, "As duas chaves estão iguais. Você colou a mesma duas vezes.")
    if not re.fullmatch(r"[A-Za-z0-9]+", key) or not re.fullmatch(r"[A-Za-z0-9]+", sec):
        raise HTTPException(400, "A chave tem caracteres estranhos. Copie de novo da página da testnet.")

    def testar():
        ex = make_exchange(settings.exchange, key, sec, testnet=True)
        bal = ex.fetch_balance()
        return float((bal.get("total") or {}).get("USDT", 0) or 0)

    try:
        usdt = await asyncio.to_thread(testar)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"A Binance de teste recusou as chaves: {str(e)[:200]}") from e
    _gravar_env({"API_KEY": key, "API_SECRET": sec, "MODE": "live", "USE_TESTNET": "true",
                 "LIVE_CONFIRM": LIVE_PHRASE})
    store.set("should_run", True)
    store.log("Chaves da testnet salvas pelo painel. Reiniciando para usar as chaves novas.")

    async def reiniciar():
        await asyncio.sleep(1.5)
        os._exit(0)  # o Docker sobe o sistema de novo sozinho (restart: unless-stopped)

    asyncio.get_running_loop().create_task(reiniciar())
    return {"ok": True, "usdt": usdt}


PAGINA_CHAVES = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CriptoGuard · Chaves</title>
<style>body{font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#0b1220;color:#e5ecf5;margin:0;padding:24px 16px}
main{max-width:520px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
h1{font-size:20px;margin:0}p{color:#9fb0c6;line-height:1.5;margin:0}
label{display:flex;flex-direction:column;gap:6px;font-size:14px;color:#9fb0c6}
input{background:#111a2e;border:1px solid #243150;color:#e5ecf5;border-radius:10px;padding:12px;font:inherit}
button{background:#22c55e;color:#04210f;border:0;border-radius:10px;padding:12px;font-weight:700;font-size:15px;cursor:pointer}
button:disabled{opacity:.5}.msg{padding:12px;border-radius:10px;display:none}.ok{background:#12301d;color:#4ade80;display:block}
.erro{background:#3a1717;color:#f87171;display:block}a{color:#60a5fa}</style></head><body><main>
<h1>Chaves da Binance de teste</h1>
<p>Cole as duas chaves da <b>testnet</b> (testnet.binance.vision). O sistema testa a conexão antes de salvar.
Depois de salvar, o robô reinicia sozinho em uns 20 segundos.</p>
<form id="f"><label>Chave da API (API Key)<input id="k" autocomplete="off" required></label>
<label>Chave secreta (Secret Key)<input id="s" type="password" autocomplete="off" required></label>
<button id="b">Testar e salvar</button></form><div id="m" class="msg"></div><p><a href="/">Voltar ao painel</a></p>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const b=document.getElementById('b'),m=document.getElementById('m');
b.disabled=true;b.textContent='Testando na Binance...';m.className='msg';
try{const r=await fetch('/api/config/chaves',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:document.getElementById('k').value,api_secret:document.getElementById('s').value})});
const j=await r.json();if(!r.ok)throw new Error(j.detail&&j.detail.map?j.detail.map(x=>x.msg).join(' '):j.detail);
m.className='msg ok';m.textContent='Chaves salvas. Saldo na testnet: '+j.usdt.toFixed(2)+' USDT. O robô reinicia em instantes.';
document.getElementById('k').value='';document.getElementById('s').value='';setTimeout(()=>location.href='/',20000)}
catch(err){m.className='msg erro';m.textContent=String(err.message||err)}finally{b.disabled=false;b.textContent='Testar e salvar'}}</script>
</main></body></html>"""


@app.get("/chaves")
def pagina_chaves():
    return HTMLResponse(PAGINA_CHAVES)


# ---------------- Painel (build do React) ----------------
DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")
