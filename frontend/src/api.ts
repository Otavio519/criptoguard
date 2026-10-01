export interface Metricas {
  capital_inicial: number
  capital_final: number
  retorno_total: number
  retorno_anual: number
  max_drawdown: number
  sharpe: number
  operacoes: number
  taxa_acerto: number
  fator_lucro: number | null
  buy_hold_retorno: number
  buy_hold_drawdown: number
  candles_travados: number
  venceu_buy_hold: boolean
  tempo_no_mercado: number
  por_estrategia: Record<string, { operacoes: number; resultado: number; taxa_acerto: number }>
  regimes: Record<string, number>
}
export interface PontoCurva { t: string; robo: number; buy_hold: number; preco?: number; regime?: string }
export interface OperacaoBT {
  entry_time: string; entry_price: number; qty: number; stop: number
  exit_time: string; exit_price: number; pnl: number; pnl_pct: number; reason: string
  strategy: string; regime: string; ret_equity: number
}
export interface MonteCarlo {
  ok: boolean; motivo?: string; simulacoes: number; operacoes_por_simulacao: number
  retorno_p5: number; retorno_mediano: number; retorno_p95: number
  drawdown_mediano: number; drawdown_p95: number
  prob_prejuizo: number; prob_drawdown_limite: number; drawdown_limite: number
  prob_ruina: number; ruina: number; veredito: string
  faixas: { n: number; p5: number; p25: number; p50: number; p75: number; p95: number }[]
  histograma: { de: number; ate: number; n: number }[]
}
export interface BacktestResult {
  metricas: Metricas; curva: PontoCurva[]; operacoes: OperacaoBT[]; fonte: string; monte_carlo: MonteCarlo
}
export interface Params {
  symbol: string; timeframe: string; since: string; until?: string | null; initial: number
  sma_fast: number; sma_slow: number; risk_per_trade: number; atr_stop_mult: number
  max_monthly_loss: number; use_regime: boolean; regime_sma: number; adx_min: number; rsi_buy: number
  demo: boolean
}
export type BacktestIn = Params
export interface WFIn extends Params { train_bars: number; test_bars: number; grid: Record<string, number[]> }
export interface Janela {
  treino_inicio: string; teste_inicio: string; teste_fim: string; parametros: string; acao: string
  retorno_treino: number | null; retorno_teste: number; buy_hold_teste: number; operacoes: number
}
export interface WFResult {
  fonte: string
  metricas: {
    janelas: number; janelas_positivas: number; janelas_em_caixa: number; capital_final: number
    retorno_total: number; retorno_anual: number; max_drawdown: number; operacoes: number; taxa_acerto: number
    buy_hold_retorno: number; buy_hold_drawdown: number; retorno_anual_treino_medio: number
    eficiencia: number | null; combinacoes_testadas: number; parametros_mais_escolhidos: [string, number][]
    veredito: string
  }
  janelas: Janela[]; curva: PontoCurva[]; operacoes: OperacaoBT[]; monte_carlo: MonteCarlo
}
export interface Posicao {
  qty: number; entry_price: number; stop: number; entry_time: string; peak?: number; strategy?: string; stop_order_id?: string
}
export interface Mercado { regime: string; candle: string; price?: number; adx: number | null; rsi: number }
export interface Moeda { symbol: string; position: Posicao | null; market: Mercado | null; last_error: string }
export interface Carteira { quote: number; assets: Record<string, number>; equity: number | null; prices: Record<string, number> }
export interface Status {
  running: boolean; mode: string; testnet: boolean; timeframe: string; symbols: string[]
  coins: Moeda[]; wallet: Carteira | null
  guard: { month: string; start_equity: number; locked: boolean } | null
  last_error: string; last_tick: string
}
export interface Config {
  exchange: string; symbol: string; symbols?: string[]; timeframe: string; mode: string; live_allowed: boolean; testnet: boolean
  params: Record<string, number | boolean>
}
export interface TradeRow { id: number; side: string; time: string; price: number; qty: number; fee: number; pnl: number | null; reason: string; symbol?: string }
export interface LogRow { time: string; level: string; msg: string }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.detail ?? `Erro ${r.status}`)
  return body as T
}

export const api = {
  config: () => req<Config>('/api/config'),
  backtest: (b: BacktestIn) => req<BacktestResult>('/api/backtest', { method: 'POST', body: JSON.stringify(b) }),
  walkforward: (b: WFIn) => req<WFResult>('/api/walkforward', { method: 'POST', body: JSON.stringify(b) }),
  grid: () => req<Record<string, number[]>>('/api/walkforward/grid'),
  status: () => req<Status>('/api/bot/status'),
  trades: () => req<TradeRow[]>('/api/bot/trades'),
  equity: () => req<{ time: string; value: number }[]>('/api/bot/equity'),
  logs: () => req<LogRow[]>('/api/bot/logs'),
  start: () => req('/api/bot/start', { method: 'POST' }),
  stop: () => req('/api/bot/stop', { method: 'POST' }),
  panic: () => req('/api/bot/panic', { method: 'POST' }),
  resetPaper: () => req('/api/bot/reset-paper', { method: 'POST' }),
}

export const pct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`
export const money = (v: number) => v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const dt = (s: string) => new Date(s).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
export const day = (s: string) => new Date(s).toLocaleDateString('pt-BR')

export const REGIME_COR: Record<string, string> = {
  alta: 'var(--pos)', baixa: 'var(--neg)', lateral: 'var(--accent)', 'sem filtro': 'var(--muted)', aquecendo: 'var(--line)',
}
export const REGIME_TXT: Record<string, string> = {
  alta: 'Alta: segue tendência', baixa: 'Baixa: fica fora', lateral: 'Lateral: reversão à média',
  'sem filtro': 'Sem filtro', aquecendo: 'Aquecendo indicadores',
}
