#!/bin/bash
# Instala o CriptoGuard num servidor Ubuntu (Oracle Cloud). Log: /var/log/criptoguard-setup.log
exec >> /var/log/criptoguard-setup.log 2>&1
set -x
export DEBIAN_FRONTEND=noninteractive
# memória extra (swap) para o build não faltar memória
if [ ! -f /swapfile ]; then fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab; fi
for i in 1 2 3 4 5; do apt-get update && apt-get install -y docker.io docker-compose-v2 git curl openssl && break; sleep 20; done
systemctl enable --now docker
# firewall do Ubuntu da Oracle: libera 80 e 443 (o 8000 fica fechado, só o Caddy fala com ele)
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
netfilter-persistent save || true
[ -d /opt/criptoguard/.git ] || git clone https://github.com/Otavio519/criptoguard.git /opt/criptoguard
cd /opt/criptoguard && git pull --ff-only || true
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  PW="CG-$(openssl rand -hex 7)"
  sed -i "s/^PANEL_PASSWORD=.*/PANEL_PASSWORD=$PW/" backend/.env
  sed -i 's/^MODE=.*/MODE=paper/' backend/.env
fi
chown 1000:1000 backend/.env && chmod 600 backend/.env
mkdir -p dados && chown -R 1000:1000 dados
IP=$(curl -s --max-time 10 https://api.ipify.org || curl -s --max-time 10 https://ifconfig.me)
echo "DOMINIO=${IP//./-}.sslip.io" > .env
docker compose --profile https up -d --build
echo "https://${IP//./-}.sslip.io" > /opt/criptoguard/ENDERECO.txt
echo FIM_INSTALACAO
