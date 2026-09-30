# CriptoGuard

Robô de trade de cripto com filtro de regime de mercado, backtest, validação walk-forward,
simulação Monte Carlo, paper trading e modo real. Roda 24h num servidor com Docker.
Backend em FastAPI + ccxt. Painel em React + TypeScript.

> Ferramenta de estudo. Resultado passado não garante resultado futuro.
> Só use dinheiro real depois de meses de simulação, e com um valor que você aceita perder.

## Como rodar

**Windows:** dê dois cliques em `iniciar.bat`. Na primeira vez ele instala tudo (precisa do Python 3.10+).
O navegador abre em http://localhost:8000.

**Linux/Mac:** `./iniciar.sh`

**Testes:** `cd backend && pytest`

**Pelo terminal:**
```
cd backend
python -m app.cli --since 2020-01-01          # backtest + Monte Carlo
python -m app.cli --walkforward               # validação fora da amostra
python -m app.cli --sem-regime                # modo clássico, só médias
python -m app.cli --symbol ETH/USDT --demo    # --demo = dados sintéticos, sem internet
```

**Servidor 24h com Docker:** veja `deploy/GUIA_SERVIDOR.md`. Resumo:
```
cp backend/.env.example backend/.env   # preencha PANEL_PASSWORD
docker compose up -d --build
```

**Mexer no painel:** `cd frontend && npm install && npm run dev` (abre em http://localhost:5173).
Depois rode `npm run build` para o backend servir a versão nova.

## Várias moedas ao mesmo tempo

No `backend/.env`:
```
SYMBOLS=BTC/USDT,ETH/USDT
TIMEFRAME=4h
TRAIL_ATR_MULT=3
USE_MEANREV=false
```
- O capital é dividido em partes iguais. Cada moeda arrisca 1% da sua fatia por operação.
- A trava de 5% ao mês vale para a carteira inteira. Se bater, o robô vende todas as moedas.
- Um erro numa moeda não para as outras.
- Todas as moedas precisam usar a mesma moeda de cotação (ex: todas em USDT).

Walk-forward da carteira BTC+ETH no gráfico de 4h (fev/2021 a set/2026), com essa configuração:
9,5% ao ano, pior queda de 11%, contra queda de 76% de quem só comprou e segurou.

## Os 4 passos

1. **Backtest.** Aba "1. Backtest". Testa a estratégia no histórico e já roda o Monte Carlo.
2. **Walk-forward.** Aba "2. Walk-forward". Confirma se a estratégia funciona em dados que o otimizador nunca viu.
3. **Simulação.** Aba "3. Robô", modo `paper` (padrão). Preço real, dinheiro fictício. Deixe de 60 a 90 dias, de preferência no servidor.
4. **Real.** Só depois dos passos anteriores. Veja "Ligando o modo real".

Regra de ouro: se o walk-forward der prejuízo ou o Monte Carlo mostrar chance de prejuízo acima de 30%, não siga para o dinheiro real.

## Filtro de regime (a parte inteligente)

A cada candle o sistema classifica o mercado:

| Regime | Como detecta | O que o robô faz |
|---|---|---|
| **Alta** | preço acima da média de 200 e ADX acima de 20 | segue a tendência: compra com a média de 20 acima da de 50 |
| **Lateral** | ADX abaixo de 20 (mercado sem direção) | reversão à média: compra abaixo da banda de Bollinger inferior com RSI abaixo de 35 e vende quando volta para a média |
| **Baixa** | preço abaixo da média de 200 e ADX acima de 20 | fica fora. Se estiver comprado, vende |

Seguir tendência em mercado lateral é o que mais faz um robô perder dinheiro. O filtro evita isso.
Desligue com `USE_REGIME=false` para voltar ao modo clássico (só cruzamento de médias).

Depois de um stop, o robô não recompra na hora. Ele espera a tendência reiniciar.

Regras que evitam "olhar o futuro": a decisão nasce no fechamento do candle e a ordem sai na abertura do próximo.
A mesma classe (`Decider`) decide no backtest e no robô. O que você testa é o que roda.

## Walk-forward

1. Separa o histórico em janelas: 365 candles de treino e 90 de teste.
2. No treino, testa todas as combinações de parâmetros (54 por padrão) e escolhe a melhor por retorno dividido pela pior queda.
3. Aplica essa combinação nos 90 candles seguintes, que ele nunca viu.
4. Se a melhor combinação do treino deu prejuízo, fica em caixa na janela de teste.
5. Emenda só as janelas de teste.

**Eficiência** = retorno anual fora da amostra dividido pelo retorno anual no treino. Acima de 50% é bom.
Abaixo de 30% indica que a estratégia decorou o passado.

## Monte Carlo

Pega o resultado de cada operação (em % do patrimônio) e sorteia 5.000 novas sequências.
Mostra o retorno no pior e no melhor 5% dos casos, a queda máxima esperada em 95% dos casos,
a chance de prejuízo e a chance de perder metade do capital. Também avisa quando o backtest teve sorte na ordem das operações.

## Gestão de risco

| Regra | Padrão | Onde muda |
|---|---|---|
| Risco por operação | 1% do capital | `RISK_PER_TRADE` |
| Stop loss | entrada menos 2 x ATR(14) | `ATR_STOP_MULT` |
| Trava mensal | perdeu 5% no mês, vende tudo e para até o mês seguinte | `MAX_MONTHLY_LOSS` |
| Alavancagem | nenhuma | fixo no código |
| Botão de pânico | para o robô e vende tudo na hora | painel |

Exemplo: capital de 1.000, entrada a 100.000 e stop a 92.000. O robô compra 0,00125 BTC (125 de posição).
Se bater no stop, a perda fica em 10, que é 1% do capital.

## Ligando o modo real

1. Crie a chave de API na corretora **SEM permissão de saque**. Ative só "leitura" e "spot trading".
2. Comece pela testnet da Binance (https://testnet.binance.vision), com `USE_TESTNET=true`.
3. No arquivo `backend/.env`:
   ```
   MODE=live
   API_KEY=sua_chave
   API_SECRET=seu_segredo
   USE_TESTNET=true
   LIVE_CONFIRM=EU ACEITO O RISCO DE PERDER DINHEIRO
   ```
4. Reinicie. Sem a frase exata em `LIVE_CONFIRM`, o modo real não liga.
5. Só troque para `USE_TESTNET=false` depois de testar tudo na testnet.

No modo real, a cada compra o robô registra uma **ordem de stop dentro da própria Binance**
(STOP_LOSS a mercado quando a corretora aceita, senão STOP_LOSS_LIMIT com limite 1% abaixo do gatilho).
O stop funciona mesmo com o computador desligado. Quando o robô volta, ele confere a ordem e registra a venda.
Com o gráfico diário, o computador só precisa ficar ligado perto do fechamento do candle (21h em Fortaleza).

## Estrutura

```
backend/
  app/
    config.py       parâmetros lidos do .env
    data.py         coleta de candles via ccxt (com cache em CSV)
    strategy.py     indicadores, filtro de regime e o Decider (regras de compra e venda)
    risk.py         tamanho da posição, stop e trava mensal
    backtest.py     motor de backtest e métricas
    walkforward.py  validação walk-forward
    montecarlo.py   simulação Monte Carlo
    broker.py       corretora simulada e real (mesma interface)
    bot.py          robô em segundo plano
    db.py           SQLite: estado, operações, patrimônio, log
    main.py         API FastAPI (com senha opcional no painel)
    cli.py          backtest e walk-forward pelo terminal
  tests/            testes automáticos (pytest)
frontend/           painel React + TypeScript + Recharts
deploy/             guia do servidor e configuração do HTTPS
Dockerfile          imagem única: painel + backend
docker-compose.yml  sobe tudo com reinício automático
```

## Imposto

Lucro com cripto tem regras de declaração na Receita Federal. Guarde o histórico de operações
(fica no SQLite em `backend/data/criptoguard.db`) e confirme as regras atuais com um contador.

## Ideias para evoluir

- Vários pares ao mesmo tempo, dividindo o risco.
- Alerta no Telegram a cada compra e venda.
- Stop enviado direto para a corretora (ordem stop-limit), além do stop do robô.
