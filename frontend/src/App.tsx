import { useCallback, useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  REGIME_COR, REGIME_TXT, api, day, dt, money, pct,
  type BacktestResult, type Config, type LogRow, type OperacaoBT, type Params, type Status, type TradeRow,
  type WFIn, type WFResult,
} from './api'
import { EquityChart, MonteCarloView, ParamsForm, Tile, tooltipStyle } from './ui'

type Aba = 'backtest' | 'wf' | 'robo'

const DEFAULTS: Params = {
  symbol: 'BTC/USDT', timeframe: '1d', since: '2020-01-01', until: '', initial: 1000,
  sma_fast: 20, sma_slow: 50, risk_per_trade: 0.01, atr_stop_mult: 2, max_monthly_loss: 0.05,
  use_regime: true, regime_sma: 200, adx_min: 20, rsi_buy: 35, demo: false,
}

function fromConfig(cfg: Config | null): Partial<Params> {
  if (!cfg) return {}
  const p = cfg.params
  return {
    symbol: cfg.symbol, timeframe: cfg.timeframe, sma_fast: Number(p.sma_fast), sma_slow: Number(p.sma_slow),
    risk_per_trade: Number(p.risk_per_trade), atr_stop_mult: Number(p.atr_stop_mult),
    max_monthly_loss: Number(p.max_monthly_loss), use_regime: Boolean(p.use_regime),
    regime_sma: Number(p.regime_sma), adx_min: Number(p.adx_min), rsi_buy: Number(p.rsi_buy),
  }
}

export default function App() {
  const [aba, setAba] = useState<Aba>('backtest')
  const [cfg, setCfg] = useState<Config | null>(null)
  useEffect(() => { api.config().then(setCfg).catch(() => setCfg(null)) }, [])

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">◆</span>
          <div>
            <h1>CriptoGuard</h1>
            <small>{cfg ? `${cfg.exchange} · ${cfg.symbol} · ${cfg.timeframe}` : 'API desconectada. Rode o backend na porta 8000.'}</small>
          </div>
        </div>
        {cfg && <span className={`badge ${cfg.mode === 'live' ? 'danger' : 'ok'}`}>
          {cfg.mode === 'live' ? `DINHEIRO REAL${cfg.testnet ? ' (testnet)' : ''}` : 'SIMULAÇÃO'}
        </span>}
      </header>
      <nav>
        <button className={aba === 'backtest' ? 'on' : ''} onClick={() => setAba('backtest')}>1. Backtest</button>
        <button className={aba === 'wf' ? 'on' : ''} onClick={() => setAba('wf')}>2. Walk-forward</button>
        <button className={aba === 'robo' ? 'on' : ''} onClick={() => setAba('robo')}>3. Robô</button>
      </nav>
      <div hidden={aba !== 'backtest'}><Backtest cfg={cfg} /></div>
      <div hidden={aba !== 'wf'}><WalkForward cfg={cfg} /></div>
      {aba === 'robo' && <Robo />}
      <footer>Ferramenta de estudo. Resultado passado não garante resultado futuro. Só opere com dinheiro que você aceita perder.</footer>
    </div>
  )
}

/* ============================ BACKTEST ============================ */
function Backtest({ cfg }: { cfg: Config | null }) {
  const [f, setF] = useState<Params>(DEFAULTS)
  useEffect(() => { setF(v => ({ ...v, ...fromConfig(cfg) })) }, [cfg])
  const [res, setRes] = useState<BacktestResult | null>(null)
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(false)

  async function rodar(demo = false) {
    setErro(''); setCarregando(true)
    try { setRes(await api.backtest({ ...f, demo, until: f.until || null })) }
    catch (e) { setErro((e as Error).message) }
    finally { setCarregando(false) }
  }

  const m = res?.metricas
  return (
    <section>
      <div className="card form">
        <ParamsForm f={f} setF={setF} />
        <div className="actions">
          <button className="primary" disabled={carregando} onClick={() => rodar(false)}>
            {carregando ? 'Calculando...' : 'Rodar com dados reais'}
          </button>
          <button disabled={carregando} onClick={() => rodar(true)}>Demo offline</button>
        </div>
      </div>
      {erro && <div className="alert">{erro}</div>}

      {m && res && <>
        <div className={`verdict ${m.venceu_buy_hold ? 'good' : 'bad'}`}>
          {m.venceu_buy_hold
            ? 'O robô rendeu mais que comprar e segurar nesse período.'
            : 'Comprar e segurar rendeu mais que o robô nesse período.'}
          <span> Pior queda: robô {pct(m.max_drawdown)} contra {pct(m.buy_hold_drawdown)}. O robô ficou
            {' '}{(m.tempo_no_mercado * 100).toFixed(0)}% do tempo comprado.</span>
          <small>Fonte: {res.fonte}. Próximo passo: confirme na aba Walk-forward.</small>
        </div>
        <div className="tiles">
          <Tile label="Capital final" value={money(m.capital_final)} sub={pct(m.retorno_total)} tone={m.retorno_total} />
          <Tile label="Retorno ao ano" value={pct(m.retorno_anual)} tone={m.retorno_anual} />
          <Tile label="Pior queda" value={pct(m.max_drawdown)} tone={-1} />
          <Tile label="Operações" value={String(m.operacoes)} sub={`acerto ${(m.taxa_acerto * 100).toFixed(0)}%`} />
          <Tile label="Fator de lucro" value={m.fator_lucro ? m.fator_lucro.toFixed(2) : '-'} sub="acima de 1 = lucro" />
          <Tile label="Comprar e segurar" value={pct(m.buy_hold_retorno)} sub={`queda ${pct(m.buy_hold_drawdown)}`} />
        </div>
        {(Object.keys(m.por_estrategia).length > 0 || Object.keys(m.regimes).length > 1) &&
          <div className="grid2 even">
            <div className="card">
              <h3>Resultado por estratégia</h3>
              <table><thead><tr><th>Estratégia</th><th>Operações</th><th>Acerto</th><th>Resultado</th></tr></thead>
                <tbody>{Object.entries(m.por_estrategia).map(([k, s]) => (
                  <tr key={k}><td>{k === 'tendencia' ? 'Seguir tendência' : 'Reversão à média'}</td><td>{s.operacoes}</td>
                    <td>{(s.taxa_acerto * 100).toFixed(0)}%</td>
                    <td className={s.resultado >= 0 ? 'pos' : 'neg'}>{money(s.resultado)}</td></tr>))}
                </tbody></table>
            </div>
            <div className="card">
              <h3>Tempo em cada regime</h3>
              {Object.entries(m.regimes).map(([k, v]) => (
                <div key={k} className="bar-row">
                  <span>{REGIME_TXT[k] ?? k}</span>
                  <div><i style={{ width: `${v * 100}%`, background: REGIME_COR[k] }} /></div>
                  <b>{(v * 100).toFixed(0)}%</b>
                </div>))}
            </div>
          </div>}
        <EquityChart data={res.curva} title="Patrimônio: robô x comprar e segurar" />
        <MonteCarloView mc={res.monte_carlo} />
        <TradesTable ops={res.operacoes} />
      </>}
    </section>
  )
}

function TradesTable({ ops }: { ops: OperacaoBT[] }) {
  return (
    <div className="card">
      <h3>Operações ({ops.length})</h3>
      <div className="table">
        <table>
          <thead><tr><th>Entrada</th><th>Estratégia</th><th>Regime</th><th>Preço</th><th>Stop</th><th>Saída</th><th>Preço</th><th>Resultado</th><th>Motivo</th></tr></thead>
          <tbody>
            {[...ops].reverse().map((t, i) => (
              <tr key={i}>
                <td>{day(t.entry_time)}</td><td>{t.strategy}</td>
                <td><i className="dot-r" style={{ background: REGIME_COR[t.regime] }} />{t.regime}</td>
                <td>{money(t.entry_price)}</td><td>{money(t.stop)}</td>
                <td>{day(t.exit_time)}</td><td>{money(t.exit_price)}</td>
                <td className={t.pnl >= 0 ? 'pos' : 'neg'}>{money(t.pnl)} ({pct(t.pnl_pct)})</td>
                <td>{t.reason}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ============================ WALK-FORWARD ============================ */
const GRID_LABEL: Record<string, string> = {
  sma_fast: 'Médias rápidas', sma_slow: 'Médias lentas', atr_stop_mult: 'Stops (x ATR)', adx_min: 'ADX mínimo',
}

function WalkForward({ cfg }: { cfg: Config | null }) {
  const [f, setF] = useState<WFIn>({ ...DEFAULTS, train_bars: 365, test_bars: 90, grid: {} })
  const [gridTxt, setGridTxt] = useState<Record<string, string>>({})
  useEffect(() => { setF(v => ({ ...v, ...fromConfig(cfg) })) }, [cfg])
  useEffect(() => {
    api.grid().then(g => setGridTxt(Object.fromEntries(Object.entries(g).map(([k, v]) => [k, v.join(', ')]))))
      .catch(() => { })
  }, [])
  const [res, setRes] = useState<WFResult | null>(null)
  const [erro, setErro] = useState('')
  const [carregando, setCarregando] = useState(false)

  const grid = Object.fromEntries(Object.entries(gridTxt)
    .filter(([k]) => f.use_regime || k !== 'adx_min')
    .map(([k, v]) => [k, v.split(',').map(x => Number(x.trim())).filter(x => !Number.isNaN(x) && x > 0)]))
  const combos = Object.values(grid).reduce((a, v) => a * Math.max(v.length, 1), 1)

  async function rodar(demo = false) {
    setErro(''); setCarregando(true)
    try { setRes(await api.walkforward({ ...f, grid, demo, until: f.until || null })) }
    catch (e) { setErro((e as Error).message) }
    finally { setCarregando(false) }
  }

  const m = res?.metricas
  return (
    <section>
      <div className="card explain">
        <b>Como funciona:</b> o sistema escolhe os melhores parâmetros numa janela de treino e aplica na janela
        seguinte, que ele nunca viu. Repete isso até o fim do histórico. O resultado emenda só as janelas de teste.
        É o teste mais honesto que existe para saber se a estratégia funciona ou só decorou o passado.
      </div>
      <div className="card form">
        <ParamsForm f={f} setF={setF} hideStrategy />
        <label>Treino (candles)<input type="number" value={f.train_bars} onChange={e => setF({ ...f, train_bars: Number(e.target.value) })} /></label>
        <label>Teste (candles)<input type="number" value={f.test_bars} onChange={e => setF({ ...f, test_bars: Number(e.target.value) })} /></label>
        <div className="grid-edit">
          <b>Parâmetros que o otimizador testa (separe por vírgula) · {combos} combinações</b>
          {Object.keys(gridTxt).filter(k => f.use_regime || k !== 'adx_min').map(k => (
            <label key={k}>{GRID_LABEL[k] ?? k}
              <input value={gridTxt[k]} onChange={e => setGridTxt({ ...gridTxt, [k]: e.target.value })} />
            </label>))}
        </div>
        <div className="actions">
          <button className="primary" disabled={carregando} onClick={() => rodar(false)}>
            {carregando ? 'Otimizando janela por janela...' : 'Rodar walk-forward'}
          </button>
          <button disabled={carregando} onClick={() => rodar(true)}>Demo offline</button>
        </div>
      </div>
      {erro && <div className="alert">{erro}</div>}

      {m && res && <>
        <div className={`verdict ${m.retorno_total > 0 && (m.eficiencia ?? 0) >= 0.3 ? 'good' : 'bad'}`}>
          {m.veredito}
          <small>Fonte: {res.fonte} · {m.combinacoes_testadas} combinações testadas em cada janela</small>
        </div>
        <div className="tiles">
          <Tile label="Fora da amostra" value={money(m.capital_final)} sub={pct(m.retorno_total)} tone={m.retorno_total} />
          <Tile label="Retorno ao ano" value={pct(m.retorno_anual)} tone={m.retorno_anual}
            sub={`no treino: ${pct(m.retorno_anual_treino_medio)}`} />
          <Tile label="Eficiência" value={m.eficiencia === null ? '-' : `${(m.eficiencia * 100).toFixed(0)}%`}
            help="Quanto do desempenho do treino sobreviveu fora da amostra. Acima de 50% é bom."
            tone={m.eficiencia === null ? undefined : m.eficiencia >= 0.5 ? 1 : -1} />
          <Tile label="Janelas positivas" value={`${m.janelas_positivas} de ${m.janelas}`}
            sub={`${m.janelas_em_caixa} em caixa`} />
          <Tile label="Pior queda" value={pct(m.max_drawdown)} tone={-1} />
          <Tile label="Comprar e segurar" value={pct(m.buy_hold_retorno)} sub={`queda ${pct(m.buy_hold_drawdown)}`} />
        </div>
        <EquityChart data={res.curva} title="Resultado fora da amostra x comprar e segurar" />
        <div className="grid2">
          <div className="card">
            <h3>Janelas</h3>
            <div className="table"><table>
              <thead><tr><th>Teste</th><th>Parâmetros escolhidos</th><th>Treino</th><th>Teste</th><th>Mercado</th><th>Ops</th></tr></thead>
              <tbody>{res.janelas.map((j, i) => (
                <tr key={i}>
                  <td>{day(j.teste_inicio)} a {day(j.teste_fim)}</td>
                  <td className={j.acao === 'caixa' ? 'muted' : ''}>{j.parametros}</td>
                  <td>{j.retorno_treino === null ? '-' : pct(j.retorno_treino)}</td>
                  <td className={j.retorno_teste >= 0 ? 'pos' : 'neg'}>{pct(j.retorno_teste)}</td>
                  <td className="muted">{pct(j.buy_hold_teste)}</td>
                  <td>{j.operacoes}</td>
                </tr>))}</tbody>
            </table></div>
          </div>
          <div className="card">
            <h3>Parâmetros mais escolhidos</h3>
            {m.parametros_mais_escolhidos.map(([p, n]) => (
              <div key={p} className="bar-row">
                <span>{p}</span>
                <div><i style={{ width: `${(n / m.janelas) * 100}%`, background: 'var(--accent)' }} /></div>
                <b>{n}x</b>
              </div>))}
            <small>Se os mesmos parâmetros aparecem muito, a estratégia é estável. Se muda toda hora, desconfie.</small>
          </div>
        </div>
        <MonteCarloView mc={res.monte_carlo} />
      </>}
    </section>
  )
}

/* ============================ ROBÔ ============================ */
function Robo() {
  const [st, setSt] = useState<Status | null>(null)
  const [trades, setTrades] = useState<TradeRow[]>([])
  const [eq, setEq] = useState<{ time: string; value: number }[]>([])
  const [logs, setLogs] = useState<LogRow[]>([])
  const [erro, setErro] = useState('')

  const load = useCallback(async () => {
    try {
      const [s, t, e, l] = await Promise.all([api.status(), api.trades(), api.equity(), api.logs()])
      setSt(s); setTrades(t); setEq(e); setLogs(l); setErro('')
    } catch (e) { setErro((e as Error).message) }
  }, [])
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id) }, [load])

  async function act(fn: () => Promise<unknown>, confirmMsg?: string) {
    if (confirmMsg && !window.confirm(confirmMsg)) return
    try { await fn(); await load() } catch (e) { setErro((e as Error).message) }
  }

  const price = st?.last_price ?? 0
  const pos = st?.position
  const aberto = pos && price ? (price - pos.entry_price) * pos.qty : 0
  const bal = st?.paper_balance
  const patrimonio = bal ? bal.quote + bal.base * price : null
  const mk = st?.market

  return (
    <section>
      {erro && <div className="alert">{erro}</div>}
      <div className="card controls">
        <div>
          <span className={`dot ${st?.running ? 'on' : ''}`} />
          <strong>{st?.running ? 'Robô rodando' : 'Robô parado'}</strong>
          {st?.last_tick && <small> · última checagem {dt(st.last_tick)}</small>}
        </div>
        <div className="actions">
          {st?.running
            ? <button onClick={() => act(api.stop)}>Pausar</button>
            : <button className="primary" onClick={() => act(api.start)}>Iniciar</button>}
          {st?.mode === 'paper' && !st.running &&
            <button onClick={() => act(api.resetPaper, 'Zerar a simulação e apagar o histórico?')}>Zerar simulação</button>}
          <button className="panic" onClick={() => act(api.panic, 'Parar o robô e VENDER a posição agora?')}>BOTÃO DE PÂNICO</button>
        </div>
      </div>
      {st?.last_error && <div className="alert">Último erro: {st.last_error}</div>}
      {st?.guard?.locked && <div className="alert">Trava mensal ativa. O robô não compra até o próximo mês.</div>}

      <div className="tiles">
        <div className="tile">
          <span>Regime atual</span>
          <strong style={{ color: REGIME_COR[mk?.regime ?? ''] }}>{mk ? mk.regime : '-'}</strong>
          <small>{mk ? REGIME_TXT[mk.regime] ?? '' : 'aparece após a 1a checagem'}
            {mk?.adx != null && ` · ADX ${mk.adx}`}{mk && ` · RSI ${mk.rsi}`}</small>
        </div>
        <Tile label="Preço atual" value={price ? money(price) : '-'} />
        {patrimonio !== null && <Tile label="Patrimônio simulado" value={money(patrimonio)} />}
        {bal && <Tile label="Saldo livre" value={money(bal.quote)} sub={`moeda: ${bal.base.toFixed(6)}`} />}
        <Tile label="Posição" value={pos ? pos.qty.toFixed(6) : 'Fora do mercado'}
          sub={pos ? `${pos.strategy ?? ''} · entrada ${money(pos.entry_price)} · stop ${money(pos.stop)}` : undefined} />
        {pos && <Tile label="Resultado aberto" value={money(aberto)} tone={aberto} />}
      </div>

      {eq.length > 1 && <div className="card">
        <h3>Patrimônio</h3>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={eq}>
            <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
            <XAxis dataKey="time" tickFormatter={dt} minTickGap={80} stroke="var(--muted)" fontSize={12} />
            <YAxis domain={['auto', 'auto']} stroke="var(--muted)" fontSize={12} tickFormatter={v => money(v)} width={80} />
            <Tooltip labelFormatter={l => dt(String(l))} formatter={v => money(Number(v))} contentStyle={tooltipStyle} />
            <Line dataKey="value" name="Patrimônio" stroke="var(--accent)" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>}

      <div className="grid2">
        <div className="card">
          <h3>Operações</h3>
          {trades.length === 0 ? <p className="muted">Nenhuma operação ainda. O robô espera o próximo sinal.</p> :
            <div className="table"><table>
              <thead><tr><th>Quando</th><th>Lado</th><th>Preço</th><th>Qtd</th><th>Resultado</th><th>Motivo</th></tr></thead>
              <tbody>{trades.map(t => (
                <tr key={t.id}>
                  <td>{dt(t.time)}</td><td className={t.side === 'compra' ? 'pos' : 'neg'}>{t.side}</td>
                  <td>{money(t.price)}</td><td>{t.qty.toFixed(6)}</td>
                  <td className={(t.pnl ?? 0) >= 0 ? 'pos' : 'neg'}>{t.pnl === null ? '-' : money(t.pnl)}</td>
                  <td>{t.reason}</td>
                </tr>))}</tbody>
            </table></div>}
        </div>
        <div className="card">
          <h3>Registro</h3>
          <ul className="logs">{logs.map((l, i) => (
            <li key={i} className={l.level}><time>{dt(l.time)}</time>{l.msg}</li>))}</ul>
        </div>
      </div>
    </section>
  )
}
