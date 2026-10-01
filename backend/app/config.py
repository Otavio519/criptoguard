"""Configuração lida do arquivo .env."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

LIVE_PHRASE = "EU ACEITO O RISCO DE PERDER DINHEIRO"
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass
class StrategyParams:
    sma_fast: int = 20
    sma_slow: int = 50
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    risk_per_trade: float = 0.01
    max_monthly_loss: float = 0.05
    fee_rate: float = 0.001
    # Filtro de regime
    use_regime: bool = True
    regime_sma: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    # Reversão à média (usada no mercado lateral)
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_buy: float = 35.0
    # Melhorias opcionais (0 / False = desligado)
    trail_atr_mult: float = 0.0     # stop móvel: maior fechamento desde a entrada - N x ATR
    vol_filter: float = 0.0         # só entra se volume > média de 20 x este fator
    use_meanrev: bool = True        # liga a reversão à média no regime lateral

    @property
    def warmup(self) -> int:
        """Candles necessários para todos os indicadores ficarem prontos."""
        base = max(self.sma_slow, self.atr_period, self.bb_period, self.rsi_period)
        if self.use_regime:
            base = max(base, self.regime_sma, self.adx_period * 3)
        return base + 2

    def validate(self) -> None:
        if not 2 <= self.sma_fast < self.sma_slow:
            raise ValueError("A média rápida precisa ser menor que a lenta (e no mínimo 2).")
        if not 0 < self.risk_per_trade <= 0.05:
            raise ValueError("Risco por operação deve ficar entre 0 e 5%.")
        if not 0 < self.max_monthly_loss <= 0.5:
            raise ValueError("Perda mensal máxima deve ficar entre 0 e 50%.")
        if self.atr_stop_mult <= 0 or self.atr_period < 2:
            raise ValueError("Parâmetros de ATR inválidos.")
        if self.regime_sma < 20 or self.adx_period < 2 or not 0 <= self.adx_min <= 60:
            raise ValueError("Parâmetros do filtro de regime inválidos.")
        if self.bb_period < 5 or self.bb_std <= 0 or not 5 <= self.rsi_buy <= 60:
            raise ValueError("Parâmetros de reversão à média inválidos.")
        if self.trail_atr_mult < 0 or self.vol_filter < 0:
            raise ValueError("Stop móvel e filtro de volume não podem ser negativos.")


@dataclass
class Settings:
    exchange: str = field(default_factory=lambda: os.getenv("EXCHANGE", "binance"))
    symbol: str = field(default_factory=lambda: os.getenv("SYMBOL", "BTC/USDT"))
    # Várias moedas ao mesmo tempo: SYMBOLS=BTC/USDT,ETH/USDT (se vazio, usa SYMBOL)
    symbols_raw: str = field(default_factory=lambda: os.getenv("SYMBOLS", ""))
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "1d"))
    mode: str = field(default_factory=lambda: os.getenv("MODE", "paper").lower())
    paper_start_balance: float = field(default_factory=lambda: _f("PAPER_START_BALANCE", 1000))
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("API_SECRET", ""))
    use_testnet: bool = field(default_factory=lambda: os.getenv("USE_TESTNET", "true").lower() == "true")
    live_confirm: str = field(default_factory=lambda: os.getenv("LIVE_CONFIRM", ""))
    db_path: Path = field(default_factory=lambda: DATA_DIR / "criptoguard.db")
    # Servidor
    panel_user: str = field(default_factory=lambda: os.getenv("PANEL_USER", ""))
    panel_password: str = field(default_factory=lambda: os.getenv("PANEL_PASSWORD", ""))
    auto_start: bool = field(default_factory=lambda: os.getenv("AUTO_START", "false").lower() == "true")
    # Estratégia: "rotacao" (rotação inteligente, padrão novo) ou "tendencia" (a antiga, gráfico de 4h)
    strategy: str = field(default_factory=lambda: os.getenv("STRATEGY", "tendencia").lower())
    rot_capital: float = field(default_factory=lambda: _f("ROT_CAPITAL", 1000))
    rot_look: int = field(default_factory=lambda: _i("ROT_LOOK", 30))
    rot_topk: int = field(default_factory=lambda: _i("ROT_TOPK", 2))
    rot_sma: int = field(default_factory=lambda: _i("ROT_SMA", 200))
    rot_vol: float = field(default_factory=lambda: _f("ROT_VOL", 0.5))
    rot_rebal: int = field(default_factory=lambda: _i("ROT_REBAL_DIAS", 7))
    rot_stop: float = field(default_factory=lambda: _f("ROT_STOP", 0.25))
    rot_trava: float = field(default_factory=lambda: _f("ROT_TRAVA", 0.10))
    params: StrategyParams = field(default_factory=lambda: StrategyParams(
        sma_fast=_i("SMA_FAST", 20),
        sma_slow=_i("SMA_SLOW", 50),
        atr_period=_i("ATR_PERIOD", 14),
        atr_stop_mult=_f("ATR_STOP_MULT", 2.0),
        risk_per_trade=_f("RISK_PER_TRADE", 0.01),
        max_monthly_loss=_f("MAX_MONTHLY_LOSS", 0.05),
        fee_rate=_f("FEE_RATE", 0.001),
        use_regime=os.getenv("USE_REGIME", "true").lower() == "true",
        regime_sma=_i("REGIME_SMA", 200),
        adx_period=_i("ADX_PERIOD", 14),
        adx_min=_f("ADX_MIN", 20),
        bb_period=_i("BB_PERIOD", 20),
        bb_std=_f("BB_STD", 2.0),
        rsi_period=_i("RSI_PERIOD", 14),
        rsi_buy=_f("RSI_BUY", 35),
        trail_atr_mult=_f("TRAIL_ATR_MULT", 0),
        vol_filter=_f("VOL_FILTER", 0),
        use_meanrev=os.getenv("USE_MEANREV", "true").lower() == "true",
    ))

    @property
    def symbols(self) -> list[str]:
        items = [x.strip().upper() for x in (self.symbols_raw or self.symbol).split(",") if x.strip()]
        seen = []
        for x in items:
            if x not in seen:
                seen.append(x)
        quotes = {x.split("/")[1] for x in seen if "/" in x}
        if len(quotes) > 1 or any("/" not in x for x in seen):
            raise ValueError(f"SYMBOLS inválido ({seen}). Use pares com a mesma moeda de cotação, ex: BTC/USDT,ETH/USDT")
        return seen

    @property
    def live_allowed(self) -> bool:
        return (
            self.mode == "live"
            and bool(self.api_key and self.api_secret)
            and self.live_confirm.strip() == LIVE_PHRASE
        )


settings = Settings()
