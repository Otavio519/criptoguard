#!/usr/bin/env python3
"""Manda o resumo do CriptoGuard para o celular pelo app ntfy (grátis, sem cadastro).
Uso: relatorio.py <tópico> [--resumo]
  sem --resumo: só avisa se houve compra/venda nova ou erro desde a última checagem
  com --resumo: manda o resumo do dia (regime, posição, stop, saldo)"""
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
        linhas = [f"Robô {'LIGADO' if st['running'] else 'DESLIGADO'} · modo {st['mode']}{' (teste)' if st.get('testnet') else ''}"]
        for c in st["coins"]:
            m, p = c.get("market") or {}, c.get("position")
            linhas.append(f"{c['symbol']}: {m.get('regime', '?')} · preço {m.get('price', 0):,.2f} · ADX {m.get('adx', '?')}")
            linhas.append(f"  posição: {'aberta, stop ' + format(p.get('stop', 0), ',.2f') if p else 'nenhuma'}")
        w = st.get("wallet") or {}
        if w.get("equity"):
            linhas.append(f"Patrimônio: {w['equity']:,.2f} USDT")
        hoje = [t for t in trades if str(t["time"])[:10] == __import__('datetime').date.today().isoformat()]
        linhas.append(f"Operações hoje: {len(hoje)}")
        enviar(topico, "CriptoGuard · resumo do dia", "\n".join(linhas))


if __name__ == "__main__":
    main()
