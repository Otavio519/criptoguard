import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { REGIME_COR, REGIME_TXT, day, money, pct, type MonteCarlo, type Params, type PontoCurva } from './api'

export const tooltipStyle = { background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 8 }

export function Tile({ label, value, sub, tone, help }: {
  label: string; value: string; sub?: string; tone?: number; help?: string
}) {
  const cls = tone === undefined ? '' : tone >= 0 ? 'pos' : 'neg'
  return (
    <div className="tile" title={help}>
      <span>{label}{help && <i className="help">?</i>}</span>
      <strong className={cls}>{value}</strong>
      {sub && <small>{sub}</small>}
    </div>
  )
}

/* ---------- formulário de parâmetros (compartilhado por backtest e walk-forward) ---------- */
export function ParamsForm<T extends Params>({ f, setF, hideStrategy = false }: {
  f: T; setF: (v: T) => void; hideStrategy?: boolean
}) {
  const set = (k: keyof Params, num = false) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: num ? Number(e.target.value) : e.target.value })
  return <>
    <label>Par<input value={f.symbol} onChange={set('symbol')} /></label>
    <label>Tempo gráfico
      <select value={f.timeframe} onChange={set('timeframe')}>
        {['1h', '4h', '1d', '1w'].map(t => <option key={t}>{t}</option>)}
      </select>
    </label>
    <label>De<input type="date" value={f.since} onChange={set('since')} /></label>
    <label>Até<input type="date" value={f.until ?? ''} onChange={set('until')} /></label>
    <label>Capital inicial<input type="number" value={f.initial} onChange={set('initial', true)} /></label>
    <label>Risco por operação<input type="number" step="0.005" value={f.risk_per_trade} onChange={set('risk_per_trade', true)} /></label>
    <label>Perda máx. no mês<input type="number" step="0.01" value={f.max_monthly_loss} onChange={set('max_monthly_loss', true)} /></label>
    {!hideStrategy && <>
      <label>Média rápida<input type="number" value={f.sma_fast} onChange={set('sma_fast', true)} /></label>
      <label>Média lenta<input type="number" value={f.sma_slow} onChange={set('sma_slow', true)} /></label>
      <label>Stop (x ATR)<input type="number" step="0.5" value={f.atr_stop_mult} onChange={set('atr_stop_mult', true)} /></label>
    </>}
    <label className="switch">
      <input type="checkbox" checked={f.use_regime} onChange={e => setF({ ...f, use_regime: e.target.checked })} />
      Filtro de regime
    </label>
    {f.use_regime && <>
      <label>Média do regime<input type="number" value={f.regime_sma} onChange={set('regime_sma', true)} /></label>
      {!hideStrategy && <label>ADX mínimo (tendência)<input type="number" value={f.adx_min} onChange={set('adx_min', true)} /></label>}
      <label>RSI de compra (lateral)<input type="number" value={f.rsi_buy} onChange={set('rsi_buy', true)} /></label>
    </>}
  </>
}

/* ---------- curva de patrimônio + faixa de regimes ---------- */
export function EquityChart({ data, title }: { data: PontoCurva[]; title: string }) {
  const hasRegime = data.some(p => p.regime && p.regime !== 'sem filtro')
  return (
    <div className="card">
      <h3>{title}</h3>
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data}>
          <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
          <XAxis dataKey="t" tickFormatter={day} minTickGap={60} stroke="var(--muted)" fontSize={12} />
          <YAxis stroke="var(--muted)" fontSize={12} tickFormatter={v => money(v)} width={80} />
          <Tooltip labelFormatter={l => day(String(l))} formatter={v => money(Number(v))} contentStyle={tooltipStyle} />
          <Legend />
          <Line dataKey="robo" name="Robô" stroke="var(--accent)" dot={false} strokeWidth={2} />
          <Line dataKey="buy_hold" name="Comprar e segurar" stroke="var(--muted)" dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
      {hasRegime && <>
        <div className="regime-strip" aria-label="Regime de mercado ao longo do tempo">
          {data.map((p, i) => <span key={i} style={{ background: REGIME_COR[p.regime ?? ''] ?? 'var(--line)' }}
            title={`${day(p.t)}: ${REGIME_TXT[p.regime ?? ''] ?? p.regime}`} />)}
        </div>
        <div className="legend-row">
          {['alta', 'lateral', 'baixa'].map(r => <span key={r}><i style={{ background: REGIME_COR[r] }} />{REGIME_TXT[r]}</span>)}
        </div>
      </>}
    </div>
  )
}

/* ---------- Monte Carlo ---------- */
export function MonteCarloView({ mc }: { mc: MonteCarlo }) {
  if (!mc.ok) return <div className="card"><h3>Monte Carlo</h3><p className="muted">{mc.motivo}</p></div>
  const fan = mc.faixas.map(f => ({ n: f.n, base: f.p5, b1: f.p25 - f.p5, b2: f.p50 - f.p25, b3: f.p75 - f.p50, b4: f.p95 - f.p75, p50: f.p50 }))
  const lo = Math.min(...mc.faixas.map(f => f.p5)), hi = Math.max(...mc.faixas.map(f => f.p95))
  const pad = (hi - lo) * 0.08 || hi * 0.02
  const hist = mc.histograma.map(h => ({ faixa: (h.de + h.ate) / 2, n: h.n }))
  return (
    <div className="card mc">
      <h3>Monte Carlo · {mc.simulacoes.toLocaleString('pt-BR')} simulações de {mc.operacoes_por_simulacao} operações</h3>
      <div className={`verdict ${mc.prob_prejuizo <= 0.15 ? 'good' : 'bad'}`}>{mc.veredito}</div>
      <div className="tiles">
        <Tile label="Retorno mediano" value={pct(mc.retorno_mediano)} tone={mc.retorno_mediano} />
        <Tile label="Pior 5% dos casos" value={pct(mc.retorno_p5)} tone={mc.retorno_p5}
          help="Em 95% das simulações o retorno ficou acima deste valor" />
        <Tile label="Melhor 5% dos casos" value={pct(mc.retorno_p95)} tone={mc.retorno_p95} />
        <Tile label="Queda máx. (95%)" value={pct(mc.drawdown_p95)} tone={-1}
          help="Em 95% das simulações a pior queda ficou menor que isso" />
        <Tile label="Chance de prejuízo" value={`${(mc.prob_prejuizo * 100).toFixed(0)}%`} tone={mc.prob_prejuizo > 0.2 ? -1 : 1} />
        <Tile label={`Queda acima de ${(mc.drawdown_limite * 100).toFixed(0)}%`} value={`${(mc.prob_drawdown_limite * 100).toFixed(1)}%`}
          sub={`perder metade: ${(mc.prob_ruina * 100).toFixed(1)}%`} />
      </div>
      <div className="grid2 even">
        <div>
          <h4>Faixas de patrimônio por operação</h4>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={fan}>
              <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
              <XAxis dataKey="n" stroke="var(--muted)" fontSize={12} />
              <YAxis stroke="var(--muted)" fontSize={12} tickFormatter={v => money(v)} width={80}
                domain={[Math.floor(lo - pad), Math.ceil(hi + pad)]} allowDataOverflow />
              <Tooltip contentStyle={tooltipStyle} formatter={(_, name, item) => {
                const f = mc.faixas[fan.indexOf(item.payload)]
                const map: Record<string, string> = { b1: `5% a 25%: ${money(f.p5)} a ${money(f.p25)}`,
                  b2: `25% a 50%: até ${money(f.p50)}`, b3: `50% a 75%: até ${money(f.p75)}`, b4: `75% a 95%: até ${money(f.p95)}` }
                return [map[String(name)] ?? '', '']
              }} labelFormatter={l => `Operação ${l}`} />
              <Area dataKey="base" stackId="1" stroke="none" fill="transparent" />
              <Area dataKey="b1" stackId="1" stroke="none" fill="var(--accent)" fillOpacity={0.15} />
              <Area dataKey="b2" stackId="1" stroke="none" fill="var(--accent)" fillOpacity={0.35} />
              <Area dataKey="b3" stackId="1" stroke="none" fill="var(--accent)" fillOpacity={0.35} />
              <Area dataKey="b4" stackId="1" stroke="none" fill="var(--accent)" fillOpacity={0.15} />
            </AreaChart>
          </ResponsiveContainer>
          <small>Faixa escura: metade central dos resultados. Faixa clara: 90% dos resultados.</small>
        </div>
        <div>
          <h4>Distribuição do retorno final</h4>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={hist}>
              <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
              <XAxis dataKey="faixa" tickFormatter={v => pct(v)} stroke="var(--muted)" fontSize={12} minTickGap={30} />
              <YAxis stroke="var(--muted)" fontSize={12} width={50} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={l => `Retorno perto de ${pct(Number(l))}`}
                formatter={v => [`${v} simulações`, '']} />
              <Bar dataKey="n">
                {hist.map((h, i) => <Cell key={i} fill={h.faixa >= 0 ? 'var(--pos)' : 'var(--neg)'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <small>Verde: simulações com lucro. Vermelho: com prejuízo.</small>
        </div>
      </div>
    </div>
  )
}
