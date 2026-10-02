#!/usr/bin/env python3
"""Diagnóstico completo do CriptoGuard (roda no servidor, sem mostrar chaves nem senhas).
Uso: sudo python3 deploy/diagnostico.py [tópico-ntfy]  -> imprime e, com tópico, manda como anexo."""
import json, subprocess, sys, urllib.request
sys.path.insert(0, "/opt/criptoguard/deploy")
import relatorio as r

def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=120).stdout.strip()

DENTRO = r'''
import json
from app.config import settings
from app.data import make_exchange
from app.db import Store
ex = make_exchange(settings.exchange, settings.api_key, settings.api_secret, settings.use_testnet)
out = {"modo": settings.mode, "testnet": settings.use_testnet, "estrategia": settings.strategy, "moedas": settings.symbols}
try:
    b = ex.fetch_balance(); out["saldo"] = {k: b["total"].get(k) for k in ["USDT", "BTC", "ETH"]}
except Exception as e: out["saldo_erro"] = str(e)[:200]
ordens = []
for s in settings.symbols:
    try:
        for o in ex.fetch_open_orders(s):
            ordens.append({"par": s, "tipo": o.get("type"), "lado": o.get("side"), "qtd": o.get("amount"), "stop": o.get("stopPrice") or o.get("triggerPrice"), "preco": o.get("price"), "id": o.get("id")})
    except Exception as e: ordens.append({"par": s, "erro": str(e)[:200]})
out["ordens_abertas"] = ordens
st = Store(settings.db_path)
c = st.get(f"rot_{settings.mode}") or {}
out["estado"] = {k: c.get(k) for k in ["cash", "hold", "entry", "stops", "stop_px", "ultimo_rebal", "ultimo_dia", "mes", "inicio_mes", "travado", "stop_tentativa"]}
print(json.dumps(out, default=str, indent=1))
'''

def main():
    L = []
    L.append("== servidor ==")
    L.append(sh("uptime; free -m | head -2; df -h / | tail -1"))
    L.append(sh("docker ps --format '{{.Names}} | {{.Status}}'"))
    L.append("== atualizações ==")
    L.append(sh("tail -4 /var/log/criptoguard-update.log; cd /opt/criptoguard && git log --oneline -1"))
    L.append("== cron ==")
    L.append(sh("crontab -l 2>/dev/null | grep -v '^#' | sed 's/criptoguard-[a-f0-9]\\{10\\}/TOPICO/'"))
    try:
        st = r.api("/api/bot/status")
        L.append("== status do painel ==")
        L.append(json.dumps({k: v for k, v in st.items() if k != "coins"}, default=str)[:1500])
        for c in st["coins"]:
            L.append(f"{c['symbol']}: market={json.dumps(c.get('market'), default=str)[:400]}")
            L.append(f"   posição={json.dumps(c.get('position'), default=str)} erro={c.get('last_error')!r}")
        L.append("== últimas notas do robô ==")
        for l in r.api("/api/bot/logs")[:25]:
            L.append(f"{str(l.get('time'))[:19]} {l.get('level')}: {str(l.get('msg'))[:220]}")
        L.append("== operações ==")
        for t in r.api("/api/bot/trades")[:20]:
            L.append(json.dumps(t, default=str)[:300])
        eq = r.api("/api/bot/equity")
        pts = eq if isinstance(eq, list) else eq.get("points", eq)
        L.append(f"== patrimônio: {len(pts)} pontos; últimos: {json.dumps(pts[-3:], default=str)[:400]}")
    except Exception as e:
        L.append(f"ERRO lendo o painel: {e}")
    L.append("== dentro do robô (corretora) ==")
    L.append(sh("docker exec -i criptoguard python - <<'PY'\n" + DENTRO + "\nPY"))
    L.append("== erros no log do container (últimas 24h) ==")
    L.append(sh("docker logs --since 24h criptoguard 2>&1 | grep -iE 'error|traceback|exception' | tail -15"))
    txt = "\n".join(L)
    print(txt)
    if len(sys.argv) > 1:
        req = urllib.request.Request(f"https://ntfy.sh/{sys.argv[1]}", data=txt.encode(), method="PUT")
        req.add_header("Filename", "diagnostico.txt"); req.add_header("Title", "CriptoGuard: diagnostico")
        urllib.request.urlopen(req, timeout=30)

if __name__ == "__main__":
    main()
