"""Estratégia "Rotação inteligente" (momentum + tendência + controle de volatilidade).

Regras (todas testadas no passado com simulação dia a dia, ver pesquisa):
- Uma vez por dia, depois que o candle diário fecha (00h UTC = 21h em Fortaleza), o robô lê o gráfico diário.
- A cada 7 dias escolhe as moedas que mais subiram nos últimos 30 dias, desde que estejam acima da média de
  200 dias e com alta positiva. Fica com as 2 melhores (ou 1, ou nenhuma, aí fica em dólar).
- O tamanho de cada compra diminui quando a moeda está muito agitada (alvo de 50% de volatilidade ao ano).
- Saída diária: se uma moeda fechar o dia abaixo da média de 200 dias, vende no mesmo dia.
- Stop de desastre registrado NA CORRETORA 25% abaixo do preço de entrada.
- Trava mensal: se o capital do robô cair 10% no mês, vende tudo e espera o mês seguinte.

O robô só mexe no capital que é dele (ROT_CAPITAL). Moedas que já estavam na conta não são tocadas.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .bot import slug
from .data import fetch_recent
from .db import Store, now_iso


@dataclass
class RotParams:
    look: int = 30          # dias para medir a força (momentum)
    topk: int = 2           # quantas moedas no máximo
    sma: int = 200          # média de tendência (dias)
    alvo_vol: float = 0.5   # volatilidade alvo ao ano (0 = desligado)
    rebal_dias: int = 7     # escolhe as moedas a cada N dias
    stop: float = 0.25      # stop de desastre abaixo da entrada (0 = desligado)
    trava: float = 0.10     # perda máxima no mês (0 = desligado)
    capital: float = 1000.0  # capital inicial do robô em USDT
    folga: float = 0.10     # não ajusta posição se a diferença for menor que 10%


def indicadores(close: pd.Series, p: RotParams) -> dict:
    """Indicadores no último candle FECHADO da série diária."""
    c = close.dropna()
    if len(c) < max(p.sma, p.look, 31) + 1:
        return {"pronto": False}
    media = c.rolling(p.sma).mean().iloc[-1]
    mom = c.iloc[-1] / c.iloc[-1 - p.look] - 1
    vol = c.pct_change().rolling(30).std().iloc[-1] * np.sqrt(365)
    return {"pronto": True, "close": float(c.iloc[-1]), "media": float(media), "mom": float(mom), "vol": float(vol),
            "acima": bool(c.iloc[-1] > media), "candle": c.index[-1].isoformat()}


def regime(ind: dict) -> str:
    if not ind.get("pronto"):
        return "aquecendo"
    if not ind["acima"]:
        return "baixa"
    return "alta" if ind["mom"] > 0 else "lateral"


def escolher(inds: dict[str, dict], p: RotParams) -> dict[str, float]:
    """Pesos-alvo (fração do capital) para cada moeda."""
    ok = sorted(((s, i) for s, i in inds.items() if i.get("pronto") and i["acima"] and i["mom"] > 0),
                key=lambda x: x[1]["mom"], reverse=True)
    pesos = {s: 0.0 for s in inds}
    for s, i in ok[:p.topk]:
        w = 1 / p.topk
        if p.alvo_vol and i["vol"] > 0:
            w *= min(1.0, p.alvo_vol / i["vol"])
        pesos[s] = w
    return pesos


class RotacaoPortfolio:
    """Mesma interface da carteira antiga (start/stop/panic/status), para o painel não precisar mudar."""

    def __init__(self, brokers: list, data_ex, store: Store, p: RotParams, poll_seconds: int = 60):
        self.brokers = {b.symbol: b for b in brokers}
        self.symbols = list(self.brokers)
        self.data_ex, self.store, self.p, self.poll = data_ex, store, p, poll_seconds
        self.mode = brokers[0].mode
        self.task: asyncio.Task | None = None
        self.last_error = ""
        self.last_tick = ""
        self.erros: dict[str, str] = {}
        self._last_equity_at = None
        self.bots: list = []  # compatibilidade com o painel antigo

    # ---------- estado ----------
    @property
    def chave(self) -> str:
        return f"rot_{self.mode}"

    def conta(self) -> dict:
        c = self.store.get(self.chave)
        if not c:
            c = {"cash": self.p.capital, "hold": {}, "entry": {}, "stops": {}, "entry_time": {},
                 "ultimo_rebal": None, "ultimo_dia": None, "mes": None, "inicio_mes": None, "travado": False}
            self.store.set(self.chave, c)
        return c

    def salvar(self, c: dict) -> None:
        self.store.set(self.chave, c)

    def log(self, msg: str, level: str = "info") -> None:
        self.store.log(msg, level)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    # ---------- ordens ----------
    def _cancelar_stop(self, c: dict, s: str) -> None:
        oid = c["stops"].pop(s, None)
        (c.get("stop_px") or {}).pop(s, None)
        b = self.brokers[s]
        if oid and getattr(b, "exchange_stop", False):
            try:
                st = b.stop_status(oid)
                if st["status"] == "closed" and st["filled"] > 0:  # o stop já vendeu
                    self._registrar_stop_executado(c, s, st)
                    return
                b.cancel_stop(oid)
            except Exception as e:  # noqa: BLE001
                self.log(f"[{s}] Não consegui cancelar o stop {oid}: {e}", "warn")

    def _registrar_stop_executado(self, c: dict, s: str, st: dict) -> None:
        qty = c["hold"].get(s, 0.0)
        pnl = (st["avg"] - c["entry"].get(s, st["avg"])) * st["filled"] - st["fee"]
        c["cash"] += st["filled"] * st["avg"] - st["fee"]
        c["hold"][s] = max(0.0, qty - st["filled"])
        self.store.add_trade(self.mode, "venda", st["avg"], st["filled"], st["fee"], pnl, "stop de desastre na corretora", s)
        self.log(f"[{s}] Stop de desastre executado na corretora a {st['avg']:.2f}.", "warn")

    def _vender(self, c: dict, s: str, qty: float, preco: float, motivo: str) -> None:
        b = self.brokers[s]
        self._cancelar_stop(c, s)
        qty = min(qty, c["hold"].get(s, 0.0))
        if qty <= 0:
            return
        filled, avg, fee = b.sell(qty, preco)
        pnl = (avg - c["entry"].get(s, avg)) * filled - fee
        c["cash"] += filled * avg - fee
        c["hold"][s] = c["hold"].get(s, 0.0) - filled
        if c["hold"][s] <= 1e-12:
            c["hold"][s] = 0.0
        self.store.add_trade(self.mode, "venda", avg, filled, fee, pnl, motivo, s)
        self.log(f"[{s}] VENDEU {filled:.6f} a {avg:.2f} ({motivo}). Resultado {pnl:+.2f} USDT.")
        if c["hold"][s] > 0:
            self._proteger(c, s)

    def _comprar(self, c: dict, s: str, valor: float, preco: float, motivo: str) -> None:
        b = self.brokers[s]
        valor = min(valor, c["cash"] * 0.995)
        if valor < 10:
            return
        self._cancelar_stop(c, s)
        filled, avg, fee = b.buy(valor / preco, preco)
        antes = c["hold"].get(s, 0.0)
        c["entry"][s] = (c["entry"].get(s, avg) * antes + avg * filled) / (antes + filled) if antes else avg
        if not antes:
            c["entry_time"][s] = now_iso()
        c["hold"][s] = antes + filled
        c["cash"] -= filled * avg + fee
        self.store.add_trade(self.mode, "compra", avg, filled, fee, None, motivo, s)
        self.log(f"[{s}] COMPROU {filled:.6f} a {avg:.2f} ({motivo}).")
        self._proteger(c, s)

    def _proteger(self, c: dict, s: str) -> None:
        b = self.brokers[s]
        if not self.p.stop or c["hold"].get(s, 0) <= 0 or not getattr(b, "exchange_stop", False):
            return
        c.setdefault("stop_px", {})
        c.setdefault("stop_tentativa", {})
        c["stop_tentativa"][s] = now_iso()
        try:
            stop = c["entry"][s] * (1 - self.p.stop)
            preco = b.price()
            piso = b.stop_floor(preco) if hasattr(b, "stop_floor") else 0.0
            if stop < piso:  # a corretora não aceita stop tão longe do preço atual
                stop = piso
            c["stops"][s] = b.place_stop(c["hold"][s], stop)
            c["stop_px"][s] = stop
            extra = " (o mais longe que a corretora aceita)" if stop == piso else ""
            self.log(f"[{s}] Stop de desastre registrado na corretora em {stop:.2f}{extra}.")
        except Exception as e:  # noqa: BLE001
            self.log(f"[{s}] Não consegui registrar o stop na corretora: {e}", "warn")

    # ---------- ciclo ----------
    def _precos(self) -> dict[str, float]:
        out = {}
        for s, b in self.brokers.items():
            try:
                out[s] = b.price()
                self.erros.pop(s, None)
            except Exception as e:  # noqa: BLE001
                self.erros[s] = str(e)
        return out

    def tick(self, agora: datetime | None = None) -> None:
        c = self.conta()
        precos = self._precos()
        if not precos:
            raise RuntimeError("; ".join(self.erros.values()) or "sem preços")

        # stop de desastre: confere se a corretora já vendeu (na simulação, o robô vigia)
        for s in list(c["stops"]) + [s for s in self.symbols if c["hold"].get(s, 0) > 0]:
            if s not in precos or c["hold"].get(s, 0) <= 0:
                continue
            b = self.brokers[s]
            if getattr(b, "exchange_stop", False) and s in c["stops"]:
                try:
                    st = b.stop_status(c["stops"][s])
                    if st["status"] == "closed" and st["filled"] > 0:
                        c["stops"].pop(s, None)
                        self._registrar_stop_executado(c, s, st)
                except Exception as e:  # noqa: BLE001
                    self.erros[s] = f"conferindo stop: {e}"
            elif self.p.stop and precos[s] <= c["entry"].get(s, 0) * (1 - self.p.stop):
                self._vender(c, s, c["hold"][s], precos[s], "stop de desastre")

        # posição sem stop na corretora: tenta de novo a cada 1 hora
        for s in self.symbols:
            b = self.brokers[s]
            if c["hold"].get(s, 0) > 0 and s not in c["stops"] and self.p.stop and getattr(b, "exchange_stop", False):
                ult = (c.get("stop_tentativa") or {}).get(s)
                if not ult or (datetime.now(timezone.utc) - datetime.fromisoformat(ult)).total_seconds() > 3600:
                    self._proteger(c, s)

        # patrimônio do robô
        equity = c["cash"] + sum(c["hold"].get(s, 0) * precos.get(s, 0) for s in self.symbols)
        now = agora or datetime.now(timezone.utc)
        self.store.set(f"wallet_{self.mode}", {"quote": c["cash"], "assets": {s.split('/')[0]: c["hold"].get(s, 0) for s in self.symbols},
                                               "equity": equity, "prices": precos})
        if self._last_equity_at is None or (now - self._last_equity_at).total_seconds() >= 600:
            self.store.add_equity(self.mode, equity)
            self._last_equity_at = now

        # trava mensal
        mes = now.strftime("%Y-%m")
        if c.get("mes") != mes:
            c.update(mes=mes, inicio_mes=equity, travado=False)
        if self.p.trava and not c["travado"] and equity < c["inicio_mes"] * (1 - self.p.trava):
            c["travado"] = True
            self.log("Trava do mês: o robô perdeu 10% neste mês. Vendendo tudo e esperando o próximo mês.", "warn")
            for s in self.symbols:
                if c["hold"].get(s, 0) > 0 and s in precos:
                    self._vender(c, s, c["hold"][s], precos[s], "trava de perda mensal")
        self.store.set(f"guard_{self.mode}", {"month": mes, "start_equity": c["inicio_mes"], "locked": c["travado"]})

        # decisão diária, depois do fechamento do candle diário (00h05 UTC)
        hoje = now.date().isoformat()
        if c.get("ultimo_dia") != hoje and now.hour * 60 + now.minute >= 5:
            self._decidir(c, precos, now)
            c["ultimo_dia"] = hoje
        self._publicar_posicoes(c, precos)
        self.salvar(c)
        if self.erros:
            raise RuntimeError("; ".join(f"[{s}] {e}" for s, e in self.erros.items()))

    def _decidir(self, c: dict, precos: dict, now: datetime) -> None:
        inds = {}
        for s in self.symbols:
            try:
                df = fetch_recent(self.data_ex, s, "1d", self.p.sma + 40)
                df = df[df.index < pd.Timestamp(now.date(), tz="UTC")]  # só candles fechados
                inds[s] = indicadores(df["close"], self.p)
            except Exception as e:  # noqa: BLE001
                self.erros[s] = f"lendo o gráfico diário: {e}"
                inds[s] = {"pronto": False}
        for s, i in inds.items():
            self.store.set(f"market_{self.mode}_{slug(s)}", {
                "regime": regime(i), "candle": i.get("candle", ""), "price": precos.get(s),
                "adx": None, "rsi": None, "mom": i.get("mom"), "vol": i.get("vol"), "media": i.get("media"),
                "estrategia": "rotacao"})
        equity = c["cash"] + sum(c["hold"].get(s, 0) * precos.get(s, 0) for s in self.symbols)
        rebal = (not c.get("ultimo_rebal")) or \
            (now.date() - datetime.fromisoformat(c["ultimo_rebal"]).date()) >= timedelta(days=self.p.rebal_dias)

        # saída diária: perdeu a média de 200 dias
        for s, i in inds.items():
            if c["hold"].get(s, 0) > 0 and i.get("pronto") and not i["acima"] and s in precos:
                self._vender(c, s, c["hold"][s], precos[s], "perdeu a média de 200 dias")

        if not rebal:
            return
        if c.get("travado"):
            self.log("Dia de escolher moedas, mas a trava do mês está ativa. Fica em dólar até o mês que vem.")
            return
        c["ultimo_rebal"] = now.date().isoformat()
        pesos = escolher(inds, self.p)
        escolhidas = [f"{s.split('/')[0]} {w * 100:.0f}%" for s, w in pesos.items() if w > 0]
        self.log("Escolha da semana: " + (", ".join(escolhidas) if escolhidas else "nenhuma moeda forte. Fica em dólar."))
        equity = c["cash"] + sum(c["hold"].get(s, 0) * precos.get(s, 0) for s in self.symbols)
        # vende primeiro, depois compra
        for s, w in pesos.items():
            if s not in precos:
                continue
            alvo = equity * w / precos[s]
            q = c["hold"].get(s, 0.0)
            if q > 0 and (w == 0 or q > alvo * (1 + self.p.folga)):
                self._vender(c, s, q if w == 0 else q - alvo, precos[s],
                             "saiu da lista das mais fortes" if w == 0 else "ajuste de tamanho")
        for s, w in sorted(pesos.items(), key=lambda x: -x[1]):
            if s not in precos or w <= 0:
                continue
            alvo_valor = equity * w
            atual = c["hold"].get(s, 0.0) * precos[s]
            if alvo_valor > atual * (1 + self.p.folga):
                self._comprar(c, s, alvo_valor - atual, precos[s],
                              f"rotação: entre as mais fortes ({inds[s]['mom'] * 100:+.0f}% em {self.p.look} dias)")

    def _publicar_posicoes(self, c: dict, precos: dict) -> None:
        for s in self.symbols:
            q = c["hold"].get(s, 0.0)
            pos = None
            if q > 0:
                pos = {"qty": q, "entry_price": c["entry"].get(s, 0),
                       "stop": (c.get("stop_px") or {}).get(s) or c["entry"].get(s, 0) * (1 - self.p.stop),
                       "entry_time": c["entry_time"].get(s, ""), "strategy": "rotação", "stop_order_id": c["stops"].get(s)}
            self.store.set(f"position_{self.mode}_{slug(s)}", pos)

    async def _loop(self) -> None:
        self.log(f"Robô iniciado em modo {self.mode.upper()} com a estratégia Rotação inteligente ({', '.join(self.symbols)}).")
        while True:
            try:
                await asyncio.to_thread(self.tick)
                self.last_error = ""
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                self.log(f"Erro: {e}", "error")
            self.last_tick = now_iso()
            await asyncio.sleep(self.poll)

    def start(self) -> None:
        if not self.running:
            self.task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            self.log("Robô parado.")

    async def panic(self) -> None:
        await self.stop()
        c = self.conta()
        for s in self.symbols:
            if c["hold"].get(s, 0) > 0:
                try:
                    preco = await asyncio.to_thread(self.brokers[s].price)
                    await asyncio.to_thread(self._vender, c, s, c["hold"][s], preco, "botão de pânico")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[{s}] Falha ao vender no pânico: {e}", "error")
        self.salvar(c)
        self.log("BOTÃO DE PÂNICO acionado.", "warn")

    def status(self) -> list[dict]:
        return [{"symbol": s, "position": self.store.get(f"position_{self.mode}_{slug(s)}"),
                 "market": self.store.get(f"market_{self.mode}_{slug(s)}"), "last_error": self.erros.get(s, "")}
                for s in self.symbols]
