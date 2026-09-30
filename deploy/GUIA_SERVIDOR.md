# Rodar o CriptoGuard 24h num servidor

Com o robô num servidor, o stop funciona mesmo com seu notebook desligado.
O Docker cuida de reiniciar tudo sozinho se o sistema cair ou o servidor reiniciar.

## 1. Contratar um servidor (VPS)

Qualquer VPS com Ubuntu 22.04 ou 24.04, 1 GB de RAM e 1 CPU resolve.
Opções comuns: Hostinger, Contabo, DigitalOcean, Vultr, Oracle Cloud (tem plano gratuito).
Prefira um servidor fora dos EUA se for usar a Binance internacional.

## 2. Instalar o Docker no servidor

Entre no servidor pelo terminal (no Windows, use o PowerShell):

```bash
ssh root@IP_DO_SERVIDOR
curl -fsSL https://get.docker.com | sh
```

## 3. Enviar o projeto

No seu computador, dentro da pasta onde está o `criptoguard`:

```bash
scp -r criptoguard root@IP_DO_SERVIDOR:/opt/
```

Ou use Git: suba o projeto num repositório privado e rode `git clone` no servidor.

## 4. Configurar

```bash
cd /opt/criptoguard
cp backend/.env.example backend/.env
nano backend/.env
```

Preencha no mínimo:

```
PANEL_USER=antonio
PANEL_PASSWORD=uma-senha-forte-de-verdade
MODE=paper
```

Sem `PANEL_PASSWORD` o sistema **se recusa a subir**. Isso evita deixar o painel aberto na internet.

## 5. Subir

```bash
docker compose up -d --build
```

Pronto. Abra `http://IP_DO_SERVIDOR:8000` no navegador. Ele pede usuário e senha.

O robô já liga sozinho (`AUTO_START=true` no `docker-compose.yml`).

## Comandos do dia a dia

| O que fazer | Comando |
|---|---|
| Ver o log ao vivo | `docker compose logs -f` |
| Ver se está saudável | `docker compose ps` (coluna STATUS mostra `healthy`) |
| Parar tudo | `docker compose down` |
| Atualizar depois de mudar o código | `docker compose up -d --build` |
| Backup | copie a pasta `dados/` (banco, histórico e cache) |

## 6. Firewall (recomendado)

```bash
ufw allow OpenSSH
ufw allow 8000/tcp
ufw enable
```

## 7. HTTPS com domínio próprio (opcional, mais seguro)

Com HTTPS a senha do painel trafega criptografada. Você precisa de um domínio (ex.: `robo.seudominio.com.br`)
apontando para o IP do servidor.

```bash
echo "DOMINIO=robo.seudominio.com.br" > .env
docker compose --profile https up -d --build
ufw allow 80/tcp && ufw allow 443/tcp && ufw delete allow 8000/tcp
```

O Caddy gera o certificado sozinho. Acesse `https://robo.seudominio.com.br`.
Para fechar a porta 8000 de vez, troque no `docker-compose.yml` a linha `"8000:8000"` por `"127.0.0.1:8000:8000"`.

## Modo real no servidor

1. Na corretora, crie a chave de API **sem permissão de saque**.
2. Se a corretora permitir, restrinja a chave ao **IP do servidor**. Assim a chave não funciona em nenhum outro lugar.
3. Teste primeiro na testnet (`USE_TESTNET=true`).
4. Preencha `MODE=live`, `API_KEY`, `API_SECRET` e `LIVE_CONFIRM` no `backend/.env` e rode `docker compose up -d`.

## O que acontece se...

- **O servidor reiniciar:** o Docker sobe o container de novo e o robô retoma de onde parou. A posição aberta, o stop e a trava mensal ficam salvos no banco em `dados/`.
- **A internet da corretora cair:** o robô registra o erro no log e tenta de novo a cada 60 segundos. Ele não morre.
- **O container travar:** o healthcheck detecta em até 3 minutos e o Docker reinicia.
- **Você apertar Pausar ou Pânico:** o robô continua parado mesmo depois de reiniciar o servidor. Só volta quando você apertar Iniciar.
