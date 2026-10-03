#!/usr/bin/env python3
"""Vigia do CriptoGuard: roda no próprio servidor a cada 10 minutos (chamado pelo auto-update.sh).
Confere a saúde do robô sem depender de nenhum computador ligado, conserta o que é seguro consertar
(reinicia o robô travado, limpa disco) e avisa no celular pelo ntfy. Nunca compra, vende ou liga o robô."""
import json, shutil, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/opt/criptoguard/deploy")
import relatorio as r

ARQ = Path("/opt/criptoguard/dados/vigia.json")
AGORA = datetime.now(timezone.utc)


def carregar():
    try:
        return json.loads(ARQ.read_text())
    except Exception:  # noqa: BLE001
        return {}


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=180)


class Vigia:
    def __init__(self, topico):
        self.topico, self.v = topico, carregar()
        self.v.setdefault("avisado", {})
        self.v.setdefault("eventos", [])
        self.v.setdefault("cont", {})

    def evento(self, msg):
        self.v["eventos"].append({"t": AGORA.isoformat(timespec="minutes"), "msg": msg})

    def avisar(self, chave, horas, titulo, msg, prio="high", tags="warning"):
        """Manda no máximo um aviso por `horas` para o mesmo problema."""
        ult = self.v["avisado"].get(chave)
        if ult and AGORA - datetime.fromisoformat(ult) < timedelta(hours=horas):
            return
        self.v["avisado"][chave] = AGORA.isoformat()
        self.evento(msg)
        try:
            r.enviar(self.topico, titulo, msg, prio, tags)
        except Exception:  # noqa: BLE001
            pass

    def contar(self, chave, ok):
        self.v["cont"][chave] = 0 if ok else self.v["cont"].get(chave, 0) + 1
        return self.v["cont"][chave]

    def reiniciar(self, motivo):
        ult = self.v.get("ult_reinicio")
        if ult and AGORA - datetime.fromisoformat(ult) < timedelta(hours=1):
            self.avisar("travado", 6, "CriptoGuard com problema",
                        f"{motivo}. Já reiniciei há menos de 1 hora e não resolveu. Preciso de ajuda para olhar.")
            return
        self.v["ult_reinicio"] = AGORA.isoformat()
        sh("docker restart criptoguard")
        self.evento(f"{motivo}. Reiniciei o robô sozinho.")
        try:
            r.enviar(self.topico, "CriptoGuard reiniciado", f"{motivo}. O vigia reiniciou o robô sozinho. "
                     "As posições e a proteção ficam salvas.", "default", "wrench")
        except Exception:  # noqa: BLE001
            pass

    def rodar(self):
        # 1) disco
        uso = shutil.disk_usage("/")
        if uso.used / uso.total > 0.85:
            sh("docker image prune -f; docker builder prune -f")
            self.avisar("disco", 24, "CriptoGuard: disco cheio", "O disco do servidor passou de 85%. Limpei arquivos velhos do Docker.")
        # 2) painel respondendo
        try:
            st = r.api("/api/bot/status")
        except Exception as e:  # noqa: BLE001
            if self.contar("api", False) >= 2:  # 20 minutos sem resposta
                self.reiniciar(f"O painel não responde há 20 minutos ({str(e)[:80]})")
                self.v["cont"]["api"] = 0
            return
        self.contar("api", True)
        # 3) robô ligado
        if not st.get("running"):
            self.avisar("pausado", 12, "CriptoGuard pausado",
                        "O robô está pausado. Ele não compra, não vende e, no modo teste, não vigia a proteção. "
                        "Se foi você que pausou, tudo bem. Senão, ligue no painel.", "default", "pause_button")
            return
        # 4) robô travado (o ciclo roda a cada 1 minuto)
        try:
            tick = datetime.fromisoformat(st.get("last_tick"))
            if AGORA - tick > timedelta(minutes=15):
                self.reiniciar(f"O robô parou de trabalhar desde {tick:%H:%M} UTC")
        except Exception:  # noqa: BLE001
            pass
        # 5) erro repetido
        if self.contar("erro", not st.get("last_error")) >= 3:  # 30 minutos com erro
            self.avisar("erro", 6, "CriptoGuard com erro", f"Erro há mais de 30 minutos: {str(st.get('last_error'))[:250]}")
        # 6) decisão diária (00:05 UTC) rodou
        if AGORA.hour >= 1:
            ontem = (AGORA - timedelta(days=1)).date().isoformat()
            velhas = [c["symbol"] for c in st.get("coins", []) if str((c.get("market") or {}).get("candle", ""))[:10] < ontem]
            if velhas:
                self.avisar("decisao", 20, "CriptoGuard: decisão do dia não rodou",
                            f"O robô não leu o gráfico de hoje para {', '.join(velhas)}.")
        # 7) perto da trava do mês
        g, w = st.get("guard") or {}, st.get("wallet") or {}
        if g.get("start_equity") and w.get("equity") and not g.get("locked"):
            queda = 1 - w["equity"] / g["start_equity"]
            if queda >= 0.07:
                self.avisar("trava", 24, "CriptoGuard perto da trava do mês",
                            f"O robô caiu {queda * 100:.1f}% no mês. Com 10% ele vende tudo e espera o mês seguinte.",
                            "default", "warning")

    def salvar(self):
        corte = (AGORA - timedelta(days=2)).isoformat()
        self.v["eventos"] = [e for e in self.v["eventos"] if e["t"] >= corte][-50:]
        self.v["ultima_ronda"] = AGORA.isoformat(timespec="minutes")
        ARQ.write_text(json.dumps(self.v))


def resumo_24h():
    """Linha para o resumo diário."""
    v = carregar()
    corte = (AGORA - timedelta(hours=24)).isoformat()
    ev = [e for e in v.get("eventos", []) if e["t"] >= corte]
    if not v.get("ultima_ronda"):
        return ""
    if not ev:
        return "Vigia: tudo certo nas últimas 24h."
    return "Vigia nas últimas 24h:\n" + "\n".join(f"  {e['t'][11:16]} UTC: {e['msg']}" for e in ev[-5:])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: vigia.py <tópico>")
    vg = Vigia(sys.argv[1])
    try:
        vg.rodar()
    finally:
        vg.salvar()
