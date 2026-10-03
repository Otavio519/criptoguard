#!/bin/bash
# Atualiza o CriptoGuard sozinho quando sai versão nova no GitHub (roda pelo cron a cada 10 min).
cd /opt/criptoguard || exit 1
exec 9>/tmp/cg-update.lock; flock -n 9 || exit 0
# Vigia: confere a saúde do robô e conserta o que é seguro (não depende de computador ligado)
TV=$( (crontab -l; cat /etc/crontab /etc/cron.d/* /var/spool/cron/crontabs/*) 2>/dev/null | grep -o 'criptoguard-[a-f0-9]\{10\}' | head -1)
[ -n "$TV" ] && timeout 120 python3 deploy/vigia.py "$TV" >> /var/log/criptoguard-vigia.log 2>&1
# Diagnóstico sob pedido: quando deploy/diagnostico.pedido muda, roda e manda num tópico separado
P=$(cat deploy/diagnostico.pedido 2>/dev/null)
if [ -n "$P" ] && [ "$P" != "$(cat dados/diag_feito 2>/dev/null)" ]; then
  T=$( (crontab -l; cat /etc/crontab /etc/cron.d/* /var/spool/cron/crontabs/*) 2>/dev/null | grep -o 'criptoguard-[a-f0-9]\{10\}' | head -1)
  [ -n "$T" ] && python3 deploy/diagnostico.py "$T-diag" > /var/log/criptoguard-diag.log 2>&1
  echo "$P" > dados/diag_feito
fi
git fetch -q origin main || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
# versão que o vigia desfez por ter quebrado o robô: não instala de novo até sair outra
[ "$(git rev-parse origin/main)" = "$(cat dados/versao_bloqueada 2>/dev/null)" ] && exit 0
echo "$(date -Is) atualizando para $(git rev-parse --short origin/main)" >> /var/log/criptoguard-update.log
git rev-parse HEAD > dados/versao_anterior
date -u +%Y-%m-%dT%H:%M:%S+00:00 > dados/ultima_atualizacao
git reset -q --hard origin/main
docker compose --profile https up -d --build >> /var/log/criptoguard-update.log 2>&1
echo "$(date -Is) pronto" >> /var/log/criptoguard-update.log
