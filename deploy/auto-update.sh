#!/bin/bash
# Atualiza o CriptoGuard sozinho quando sai versão nova no GitHub (roda pelo cron a cada 10 min).
cd /opt/criptoguard || exit 1
exec 9>/tmp/cg-update.lock; flock -n 9 || exit 0
git fetch -q origin main || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
echo "$(date -Is) atualizando para $(git rev-parse --short origin/main)" >> /var/log/criptoguard-update.log
git reset -q --hard origin/main
docker compose --profile https up -d --build >> /var/log/criptoguard-update.log 2>&1
echo "$(date -Is) pronto" >> /var/log/criptoguard-update.log
