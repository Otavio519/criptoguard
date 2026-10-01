#!/bin/bash
# Roda no Cloud Shell da Oracle: cria rede, portas e a máquina grátis do CriptoGuard em São Paulo.
set -e
T="${OCI_TENANCY:-$(grep -m1 '^tenancy' ~/.oci/config 2>/dev/null | cut -d= -f2)}"
[ -n "$T" ] || T=$(oci iam compartment list --all --query 'data[0]."compartment-id"' --raw-output)
echo "Conta: $T"
[ -f ~/.ssh/cg ] || ssh-keygen -t rsa -b 4096 -N "" -f ~/.ssh/cg -q
curl -fsSL https://raw.githubusercontent.com/Otavio519/criptoguard/main/deploy/cloud-init.sh -o ~/cg-cloud-init.sh

VCN=$(oci network vcn list -c "$T" --display-name cg-vcn --query 'data[0].id' --raw-output 2>/dev/null || true)
if [ -z "$VCN" ] || [ "$VCN" = "null" ]; then
  echo "Criando a rede..."
  VCN=$(oci network vcn create -c "$T" --cidr-block 10.0.0.0/16 --display-name cg-vcn --dns-label cgvcn --wait-for-state AVAILABLE --query data.id --raw-output)
  IGW=$(oci network internet-gateway create -c "$T" --vcn-id "$VCN" --is-enabled true --display-name cg-igw --wait-for-state AVAILABLE --query data.id --raw-output)
  RT=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-route-table-id"' --raw-output)
  oci network route-table update --rt-id "$RT" --force --route-rules "[{\"destination\":\"0.0.0.0/0\",\"networkEntityId\":\"$IGW\"}]" >/dev/null
fi
SL=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-security-list-id"' --raw-output)
echo "Abrindo as portas 22, 80 e 443..."
oci network security-list update --security-list-id "$SL" --force \
  --ingress-security-rules '[{"source":"0.0.0.0/0","protocol":"6","tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},{"source":"0.0.0.0/0","protocol":"6","tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},{"source":"0.0.0.0/0","protocol":"6","tcpOptions":{"destinationPortRange":{"min":443,"max":443}}}]' \
  --egress-security-rules '[{"destination":"0.0.0.0/0","protocol":"all"}]' >/dev/null
SUB=$(oci network subnet list -c "$T" --vcn-id "$VCN" --query 'data[0].id' --raw-output 2>/dev/null || true)
if [ -z "$SUB" ] || [ "$SUB" = "null" ]; then
  SUB=$(oci network subnet create -c "$T" --vcn-id "$VCN" --cidr-block 10.0.0.0/24 --display-name cg-sub --dns-label cgsub --wait-for-state AVAILABLE --query data.id --raw-output)
fi
AD=$(oci iam availability-domain list -c "$T" --query 'data[0].name' --raw-output)

ID=$(oci compute instance list -c "$T" --display-name criptoguard --lifecycle-state RUNNING --query 'data[0].id' --raw-output 2>/dev/null || true)
if [ -z "$ID" ] || [ "$ID" = "null" ]; then
  for SHAPE in VM.Standard.A1.Flex VM.Standard.E2.1.Micro; do
    if [ "$SHAPE" = VM.Standard.A1.Flex ]; then CFG=(--shape-config '{"ocpus":1,"memoryInGBs":6}'); else CFG=(); fi
    IMG=$(oci compute image list -c "$T" --operating-system "Canonical Ubuntu" --operating-system-version "24.04" --shape $SHAPE --sort-by TIMECREATED --query 'data[0].id' --raw-output)
    echo "Criando a máquina ($SHAPE)..."
    if ID=$(oci compute instance launch -c "$T" --availability-domain "$AD" --shape $SHAPE "${CFG[@]}" \
        --image-id "$IMG" --subnet-id "$SUB" --assign-public-ip true --display-name criptoguard \
        --ssh-authorized-keys-file ~/.ssh/cg.pub --user-data-file ~/cg-cloud-init.sh \
        --wait-for-state RUNNING --query data.id --raw-output 2>/tmp/cg-erro.txt); then break; fi
    echo "Não deu com $SHAPE:"; grep -o '"message": "[^"]*"' /tmp/cg-erro.txt | head -2; ID=""
  done
fi
[ -n "$ID" ] || { echo "FALHOU: sem capacidade agora. Rode de novo mais tarde."; exit 1; }
IP=$(oci compute instance list-vnics --instance-id "$ID" --query 'data[0]."public-ip"' --raw-output)
echo "$IP" > ~/cg-ip.txt
echo "MAQUINA_PRONTA IP=$IP  painel: https://${IP//./-}.sslip.io  (a instalação leva uns 10 minutos)"
