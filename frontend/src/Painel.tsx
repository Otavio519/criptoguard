import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, money, type Config, type LogRow, type Moeda, type Status, type TradeRow } from './api'
import { tooltipStyle } from './ui'

/* ---------------- textos simples ---------------- */
const CLIMA: Record<string, { titulo: string; frase: string; tom: 'pos' | 'warn' | 'neg' | 'muted'; icone: string }> = {
  alta: { titulo: 'Subindo com força', frase: 'O robô pode comprar quando aparecer o sinal.', tom: 'pos', icone: '↗' },
  lateral: { titulo: 'Sem direção', frase: 'Preço indo e voltando. O robô espera.', tom: 'warn', icone: '→' },
  baixa: { titulo: 'Caindo', frase: 'O robô fica de fora para proteger o dinheiro.', tom: 'neg', icone: '↘' },
  aquecendo: { titulo: 'Juntando dados', frase: 'Ainda lendo o histórico do preço.', tom: 'muted', icone: '…' },
  'sem filtro': { titulo: 'Sem filtro', frase: 'Filtro de mercado desligado.', tom: 'muted', icone: '•' },
}
const NOME_MOEDA: Record<string, string> = { BTC: 'Bitcoin', ETH: 'Ethereum', SOL: 'Solana', BNB: 'BNB', XRP: 'XRP' }
const TF_MS: Record<string, number> = { '15m': 9e5, '1h': 36e5, '4h': 144e5, '1d': 864e5 }

function haQuanto(iso: string) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'agora mesmo'
  if (s < 3600) return `há ${Math.round(s / 60)} min`
  if (s < 86400) return `há ${Math.round(s / 3600)} h`
  return `há ${Math.round(s / 86400)} dias`
}
const hora = (d: Date) => d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
const usd = (v: number) => `US$ ${money(v)}`
const base = (sym: string) => sym.split('/')[0]

function proximaAnalise(candle: string | undefined, tf: string) {
  const ms = TF_MS[tf]
  if (!candle || !ms) return null
  let t = new Date(candle).getTime() + 2 * ms // o candle registrado é o último FECHADO; o próximo fecha 2 períodos depois do início dele
  while (t < Date.now()) t += ms
  return new Date(t)
}

/* ---------------- peças ---------------- */
function Ajuda({ children }: { children: ReactNode }) {
  return <span className="ajuda" tabIndex={0}>?<span className="balao">{children}</span></span>
}

function Confirmar({ titulo, texto, botao, perigo, onOk, onCancel }: {
  titulo: string; texto: string; botao: string; perigo?: boolean; onOk: () => void; onCancel: () => void
}) {
  return (
    <div className="modal-fundo" onMouseDown={e => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="mt">
        <h3 id="mt">{titulo}</h3>
        <p>{texto}</p>
        <div className="linha-fim">
          <button className="btn" onClick={onCancel}>Cancelar</button>
          <button className={`btn ${perigo ? 'perigo' : 'primario'}`} onClick={onOk} autoFocus>{botao}</button>
        </div>
      </div>
    </div>
  )
}

function ForcaTendencia({ adx, minimo }: { adx: number | null; minimo: number }) {
  const v = adx ?? 0
  const max = 50
  const nivel = v < minimo ? 'fraca' : v < 35 ? 'boa' : 'forte'
  return (
    <div className="forca">
      <div className="forca-topo">
        <span>Força da tendência <Ajuda>Indicador ADX. Abaixo de {minimo} o mercado está sem direção e o robô não segue tendência.</Ajuda></span>
        <b>{adx == null ? '-' : `${adx.toFixed(0)} · ${nivel}`}</b>
      </div>
      <div className="trilho" role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={v} aria-label="Força da tendência">
        <i style={{ width: `${Math.min(100, (v / max) * 100)}%` }} className={v >= minimo ? 'ok' : ''} />
        <em style={{ left: `${(minimo / max) * 100}%` }} title={`Mínimo para operar: ${minimo}`} />
      </div>
      <div className="forca-legenda"><span>fraca</span><span>mínimo {minimo}</span><span>forte</span></div>
    </div>
  )
}

function CartaoMoeda({ m, preco, adxMin }: { m: Moeda; preco: number; adxMin: number }) {
  const b = base(m.symbol)
  const mk = m.market
  const clima = CLIMA[mk?.regime ?? 'aquecendo'] ?? CLIMA.aquecendo
  const pos = m.position
  const aberto = pos ? (preco - pos.entry_price) * pos.qty : 0
  const abertoPct = pos ? (preco / pos.entry_price - 1) * 100 : 0
  // posição do preço entre o stop e o maior preço já visto (ou +10% da entrada)
  const topo = pos ? Math.max(pos.peak ?? 0, pos.entry_price * 1.1, preco) : 0
  const barra = pos ? Math.max(0, Math.min(100, ((preco - pos.stop) / (topo - pos.stop)) * 100)) : 0
  return (
    <article className="cartao moeda">
      <header>
        <span className={`ficha f-${b.toLowerCase()}`}>{b}</span>
        <div>
          <h3>{NOME_MOEDA[b] ?? b}</h3>
          <small>{m.symbol}</small>
        </div>
        <strong className="preco">{preco ? usd(preco) : '-'}</strong>
      </header>

      <div className={`clima t-${clima.tom}`}>
        <span className="clima-icone" aria-hidden>{clima.icone}</span>
        <div><b>{clima.titulo}</b><span>{clima.frase}</span></div>
      </div>

      <ForcaTendencia adx={mk?.adx ?? null} minimo={adxMin} />

      {pos ? (
        <div className="posicao">
          <div className="pos-topo">
            <span className="etiqueta pos">Comprado</span>
            <b className={aberto >= 0 ? 'c-pos' : 'c-neg'}>{aberto >= 0 ? '+' : ''}{usd(aberto)} ({abertoPct >= 0 ? '+' : ''}{abertoPct.toFixed(1)}%)</b>
          </div>
          <dl>
            <div><dt>Quantidade</dt><dd>{pos.qty.toFixed(6)} {b}</dd></div>
            <div><dt>Comprou a</dt><dd>{usd(pos.entry_price)}</dd></div>
            <div><dt>Proteção (stop) <Ajuda>Se o preço cair até aqui, a corretora vende sozinha. Isso limita a perda.</Ajuda></dt><dd className="c-neg">{usd(pos.stop)}</dd></div>
          </dl>
          <div className="trilho stop" aria-label="Distância até a proteção"><i style={{ width: `${barra}%` }} /></div>
          <small className="muted">{barra < 25 ? 'Perto da proteção.' : 'Distante da proteção.'} {pos.stop_order_id ? 'Ordem de proteção registrada na corretora.' : ''}</small>
        </div>
      ) : (
        <div className="posicao vazia">
          <span className="etiqueta">Sem compra aberta</span>
          <small className="muted">{mk?.regime === 'alta' ? 'Esperando o sinal de entrada.' : 'Esperando o mercado subir com força.'}</small>
        </div>
      )}
      {m.last_error && <p className="erro-mini">⚠ {m.last_error}</p>}
    </article>
  )
}

/* ---------------- painel ---------------- */
export default function Painel({ cfg }: { cfg: Config | null }) {
  const [st, setSt] = useState<Status | null>(null)
  const [trades, setTrades] = useState<TradeRow[]>([])
  const [eq, setEq] = useState<{ time: string; value: number }[]>([])
  const [logs, setLogs] = useState<LogRow[]>([])
  const [erro, setErro] = useState('')
  const [pedido, setPedido] = useState<null | 'pausar' | 'panico'>(null)
  const [, tique] = useState(0)

  const load = useCallback(async () => {
    try {
      const [s, t, e, l] = await Promise.all([api.status(), api.trades(), api.equity(), api.logs()])
      setSt(s); setTrades(t); setEq(e); setLogs(l); setErro('')
    } catch (e) { setErro((e as Error).message) }
  }, [])
  useEffect(() => {
    load()
    const a = setInterval(load, 10000), b = setInterval(() => tique(x => x + 1), 30000)
    return () => { clearInterval(a); clearInterval(b) }
  }, [load])

  async function act(fn: () => Promise<unknown>) {
    setPedido(null)
    try { await fn(); await load() } catch (e) { setErro((e as Error).message) }
  }

  const p = cfg?.params ?? {}
  const adxMin = Number(p.adx_min ?? 20)
  const perdaMax = Number(p.max_monthly_loss ?? 0.05)
  const risco = Number(p.risk_per_trade ?? 0.01)
  const preco = (m: Moeda) => st?.wallet?.prices?.[m.symbol] ?? m.market?.price ?? 0
  const patrimonio = st?.wallet?.equity ?? null
  const inicioMes = st?.guard?.start_equity ?? 0
  const varMes = patrimonio && inicioMes ? patrimonio / inicioMes - 1 : null
  const usoTrava = varMes !== null && varMes < 0 ? Math.min(1, -varMes / perdaMax) : 0
  const comPosicao = st?.coins.filter(c => c.position) ?? []
  const prox = proximaAnalise(st?.coins[0]?.market?.candle, st?.timeframe ?? '')
  const teste = st?.mode !== 'live' || st?.testnet

  const frase = useMemo(() => {
    if (!st) return 'Carregando...'
    if (!st.running) return 'O robô está pausado. Ele não compra nem vende até você ligar de novo.'
    if (st.guard?.locked) return 'A trava do mês foi acionada: o robô perdeu o limite do mês e só volta a comprar no mês que vem.'
    if (comPosicao.length) {
      return `O robô está com compra aberta em ${comPosicao.map(c => NOME_MOEDA[base(c.symbol)] ?? base(c.symbol)).join(' e ')}. A proteção (stop) já está na corretora e sobe junto com o preço.`
    }
    const regimes = st.coins.map(c => c.market?.regime ?? 'aquecendo')
    if (regimes.every(r => r === 'lateral')) return 'O mercado está sem direção. O robô está esperando uma alta firme para comprar. Ficar de fora agora é proteção.'
    if (regimes.every(r => r === 'baixa')) return 'O mercado está caindo. O robô fica de fora e guarda o dinheiro.'
    if (regimes.includes('alta')) return 'Uma das moedas está subindo com força. O robô está atento ao sinal de compra.'
    return 'O robô está acompanhando o mercado e espera a hora certa de comprar.'
  }, [st, comPosicao])

  const curva = eq.map(e => ({ t: e.time, v: e.value }))
  const ultimosLogs = logs.slice(0, 30)

  return (
    <main className="painel">
      {erro && <div className="aviso perigo">Não consegui falar com o robô: {erro}</div>}
      {st?.last_error && <div className="aviso perigo">Último problema: {st.last_error}</div>}

      {/* ---------- herói ---------- */}
      <section className="heroi cartao">
        <div className="heroi-esq">
          <div className={`estado ${st?.running ? 'ligado' : 'parado'}`}>
            <span className="pulso" aria-hidden />
            {st ? (st.running ? 'Robô trabalhando' : 'Robô pausado') : 'Conectando...'}
          </div>
          <p className="frase">{frase}</p>
          <div className="meta">
            {st?.last_tick && <span>Última checagem {haQuanto(st.last_tick)}</span>}
            {st?.running && prox && <span>Próxima decisão às {hora(prox)}</span>}
            <span className={`modo ${teste ? 'teste' : 'real'}`}>{st?.mode === 'live' ? (st.testnet ? 'Binance de teste · dinheiro fictício' : 'DINHEIRO REAL') : 'Simulação'}</span>
          </div>
        </div>
        <div className="heroi-dir">
          <span className="rotulo">Patrimônio {teste ? 'de teste' : ''}</span>
          <strong className="grande">{patrimonio != null ? usd(patrimonio) : '-'}</strong>
          {varMes !== null && <span className={`var ${varMes >= 0 ? 'c-pos' : 'c-neg'}`}>{varMes >= 0 ? '▲' : '▼'} {(Math.abs(varMes) * 100).toFixed(2)}% este mês</span>}
          <div className="botoes">
            {st?.running
              ? <button className="btn" onClick={() => setPedido('pausar')}>Pausar robô</button>
              : <button className="btn primario" onClick={() => act(api.start)}>Ligar robô</button>}
            <button className="btn perigo-contorno" onClick={() => setPedido('panico')}>Emergência</button>
          </div>
        </div>
      </section>

      {/* ---------- moedas ---------- */}
      <section className="grade-moedas">
        {st?.coins.map(m => <CartaoMoeda key={m.symbol} m={m} preco={preco(m)} adxMin={adxMin} />)}
      </section>

      {/* ---------- proteções ---------- */}
      <section className="cartao protecoes">
        <h2>Como o seu dinheiro fica protegido</h2>
        <ul>
          <li><span className="ok-marca">✓</span><div><b>Proteção em cada compra</b><small>Toda compra já nasce com uma ordem de venda automática (stop) registrada na corretora. Funciona mesmo se o servidor cair.</small></div></li>
          <li><span className="ok-marca">✓</span><div><b>Risco pequeno por operação</b><small>Cada compra arrisca no máximo {(risco * 100).toFixed(0)}% do patrimônio se a proteção for acionada.</small></div></li>
          <li>
            <span className={`ok-marca ${st?.guard?.locked ? 'alerta' : ''}`}>{st?.guard?.locked ? '!' : '✓'}</span>
            <div style={{ flex: 1 }}>
              <b>Limite de perda no mês: {(perdaMax * 100).toFixed(0)}%</b>
              <small>{st?.guard?.locked ? 'Limite atingido. O robô só volta a comprar no próximo mês.' : `Usado até agora: ${(usoTrava * 100).toFixed(0)}% do limite.`}</small>
              <div className="trilho trava" aria-label="Uso do limite mensal"><i style={{ width: `${usoTrava * 100}%` }} /></div>
            </div>
          </li>
        </ul>
      </section>

      {/* ---------- gráfico ---------- */}
      <section className="cartao">
        <h2>Evolução do patrimônio</h2>
        {curva.length > 1 ? (
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={curva} margin={{ left: 0, right: 8, top: 8 }}>
              <defs>
                <linearGradient id="gEq" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--marca)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--marca)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--grade)" vertical={false} />
              <XAxis dataKey="t" tickFormatter={s => new Date(s).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })} minTickGap={60} stroke="var(--fraco)" fontSize={12} tickLine={false} axisLine={false} />
              <YAxis domain={['auto', 'auto']} stroke="var(--fraco)" fontSize={12} tickFormatter={v => `${(v / 1000).toFixed(1)}k`} width={48} tickLine={false} axisLine={false} />
              <Tooltip labelFormatter={l => new Date(String(l)).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })} formatter={v => [usd(Number(v)), 'Patrimônio']} contentStyle={tooltipStyle} />
              <Area dataKey="v" stroke="var(--marca)" strokeWidth={2} fill="url(#gEq)" />
            </AreaChart>
          </ResponsiveContainer>
        ) : <p className="muted">O gráfico aparece depois das primeiras horas do robô ligado.</p>}
      </section>

      {/* ---------- operações e registro ---------- */}
      <section className="grade-2">
        <div className="cartao">
          <h2>Compras e vendas</h2>
          {trades.length === 0 ? (
            <div className="vazio">
              <span aria-hidden>⌛</span>
              <p>Nenhuma operação ainda.<br /><small>O robô compra só quando o mercado sobe com força. Pode levar dias.</small></p>
            </div>
          ) : (
            <ul className="lista-ops">
              {trades.slice(0, 40).map(t => {
                const compra = t.side === 'compra' || t.side === 'buy'
                return (
                  <li key={t.id}>
                    <span className={`seta ${compra ? 'compra' : 'venda'}`}>{compra ? '↓' : '↑'}</span>
                    <div>
                      <b>{compra ? 'Comprou' : 'Vendeu'} {Number(t.qty).toFixed(6)} {base(t.symbol ?? '')}</b>
                      <small>{new Date(t.time).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })} · a {usd(t.price)} · {t.reason}</small>
                    </div>
                    {t.pnl !== null && <strong className={t.pnl >= 0 ? 'c-pos' : 'c-neg'}>{t.pnl >= 0 ? '+' : ''}{usd(t.pnl)}</strong>}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
        <div className="cartao">
          <h2>O que o robô anotou</h2>
          <ul className="diario">
            {ultimosLogs.map((l, i) => (
              <li key={i} className={l.level}>
                <time>{haQuanto(l.time)}</time>
                <span>{l.msg}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {pedido === 'pausar' && <Confirmar titulo="Pausar o robô?" botao="Pausar"
        texto="Ele para de comprar e vender. Se tiver compra aberta, ela continua aberta e a proteção (stop) continua na corretora."
        onOk={() => act(api.stop)} onCancel={() => setPedido(null)} />}
      {pedido === 'panico' && <Confirmar titulo="Vender tudo e desligar?" botao="Vender tudo agora" perigo
        texto="O robô vende na hora todas as compras abertas, pelo preço do momento, e desliga. Use só em emergência."
        onOk={() => act(api.panic)} onCancel={() => setPedido(null)} />}
    </main>
  )
}
