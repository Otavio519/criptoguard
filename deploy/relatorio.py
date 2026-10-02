#!/usr/bin/env python3
"""Manda o resumo do CriptoGuard para o celular pelo app ntfy (grátis, sem cadastro).
Uso: relatorio.py <tópico> [--resumo]
  sem --resumo: só avisa se houve compra/venda nova ou erro desde a última checagem
  com --resumo: manda o resumo do dia (clima, posição, proteção, saldo)"""
import base64, json, os, sys, urllib.request
from pathlib import Path

BASE = Path("/opt/criptoguard")
ESTADO = BASE / "dados" / "relatorio_estado.json"


def env(k):
    for linha in (BASE / "backend" / ".env").read_text().splitlines():
        if linha.startswith(k + "="):
            return linha.split("=", 1)[1].strip()
    return ""


def api(path):
    req = urllib.request.Request("http://127.0.0.1:8000" + path)
    tok = base64.b64encode(f"{env('PANEL_USER') or 'admin'}:{env('PANEL_PASSWORD')}".encode()).decode()
    req.add_header("Authorization", "Basic " + tok)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def enviar(topico, titulo, texto, prioridade="default", tags="chart_with_upwards_trend"):
    req = urllib.request.Request(f"https://ntfy.sh/{topico}", data=texto.encode(), method="POST")
    req.add_header("Title", titulo)
    req.add_header("Priority", prioridade)
    req.add_header("Tags", tags)
    urllib.request.urlopen(req, timeout=20)


def main():
    topico = sys.argv[1]
    resumo = "--resumo" in sys.argv
    try:
        st, trades, logs = api("/api/bot/status"), api("/api/bot/trades"), api("/api/bot/logs")
    except Exception as e:  # noqa: BLE001
        enviar(topico, "CriptoGuard fora do ar", f"O painel não respondeu no servidor: {e}", "high", "warning")
        return
    estado = json.loads(ESTADO.read_text()) if ESTADO.exists() else {"ult_trade": 0, "ult_erro": ""}
    novos = [t for t in trades if t["id"] > estado.get("ult_trade", 0)]
    for t in reversed(novos):
        lado = "COMPROU" if t["side"] in ("compra", "buy") else "VENDEU"
        msg = f"{lado} {float(t['qty']):.6f} {t['symbol']} a {float(t['price']):.2f} USDT ({t['reason']})"
        if t.get("pnl"):
            msg += f"\nResultado: {t['pnl']:+.2f} USDT"
        enviar(topico, f"CriptoGuard {lado.lower()}", msg, "high", "moneybag")
    if trades:
        estado["ult_trade"] = max(t["id"] for t in trades)
    erros = [x for x in logs if x.get("level") == "error"]
    if erros and erros[0]["time"] != estado.get("ult_erro"):
        estado["ult_erro"] = erros[0]["time"]
        enviar(topico, "CriptoGuard com erro", erros[0]["msg"][:300], "high", "warning")
    ESTADO.write_text(json.dumps(estado))
    if resumo:
        enviar(topico, "CriptoGuard: resumo do dia", montar_resumo(st, trades))


CLIMA = {"alta": "subindo", "baixa": "caindo", "lateral": "parado", "aquecendo": "juntando dados"}


def montar_resumo(st, trades):
    import datetime as dt
    limite = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
    linhas = [f"Robô {'LIGADO' if st['running'] else 'DESLIGADO'}{' (modo teste, dinheiro fictício)' if st.get('testnet') else ''}", ""]
    for c in st["coins"]:
        m, p = c.get("market") or {}, c.get("position")
        moeda, preco = c["symbol"].split("/")[0], float(m.get("price") or 0)
        linhas.append(f"{moeda}: {CLIMA.get(m.get('regime'), m.get('regime', '?'))}, preço {preco:,.2f} USDT")
        if p:
            ent = float(p.get("entry_price") or 0)
            var = (preco / ent - 1) * 100 if ent and preco else 0
            linhas.append(f"  Você tem {float(p['qty']):.6f} {moeda}. Comprou a {ent:,.2f}. Agora {var:+.1f}%")
            linhas.append(f"  Proteção vende se cair a {float(p.get('stop') or 0):,.2f}")
        else:
            linhas.append("  Sem compra nessa moeda")
    w = st.get("wallet") or {}
    if w.get("equity"):
        linhas += ["", f"Patrimônio: {w['equity']:,.2f} USDT"]
    linhas.append(f"Operações nas últimas 24h: {len([t for t in trades if str(t['time']) >= limite])}")
    return "\n".join(linhas)

if __name__ == "__main__":
    main()
