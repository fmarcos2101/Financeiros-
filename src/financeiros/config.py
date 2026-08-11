from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExchangeConfig(BaseModel):
    name: str = "binance"
    base_url: str = "https://data-api.binance.vision"
    timeout_seconds: float = 15.0


class UniverseConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    interval: str = "1h"
    lookback_candles: int = 120


class AnalysisConfig(BaseModel):
    fast_sma: int = 12
    slow_sma: int = 26
    min_signal_strength: float = 0.001  # spread mínimo |fast-slow|/preço (~0.1%)
    max_volatility: float = 0.08
    # Filtros de qualidade de entrada (defaults calibrados por backtest)
    require_trend_filter: bool = True  # BUY só com preço > SMA lenta
    require_slow_slope: bool = False
    slope_lookback: int = 5
    require_momentum: bool = False
    rsi_period: int = 14
    rsi_buy_min: float = 45.0
    rsi_buy_max: float = 68.0
    rsi_sell_max: float = 55.0
    require_volume: bool = True
    volume_ma_period: int = 20
    volume_factor: float = 1.5


class ExitsConfig(BaseModel):
    """Saídas automáticas por posição (prioridade sobre sinal de entrada)."""

    enabled: bool = True
    stop_loss_pct: float = Field(default=0.04, ge=0.0, le=1.0)
    take_profit_pct: float = Field(default=0.06, ge=0.0, le=5.0)
    # Trailing: depois de activation_pct de lucro, stop sobe com o pico
    trailing_enabled: bool = True
    trailing_pct: float = Field(default=0.03, ge=0.0, le=1.0)
    trailing_activation_pct: float = Field(default=0.025, ge=0.0, le=1.0)


class CapitalConfig(BaseModel):
    starting_cash_usdt: float = 1000.0
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.20
    max_open_positions: int = 2
    min_notional_usdt: float = 10.0
    # Fundo reserva: fatia do lucro realizado (SELL com PnL > 0) sai do caixa de risco
    reserve_enabled: bool = True
    reserve_skim_pct: float = Field(default=0.20, ge=0.0, le=1.0)


class MemoryConfig(BaseModel):
    db_path: str = "data/memory/financeiros.db"


class ExecutionConfig(BaseModel):
    fee_bps: float = 10.0


class RuntimeConfig(BaseModel):
    cycle_interval_seconds: int = 3600
    log_dir: str = "data/logs"


class CircuitBreakerConfig(BaseModel):
    """Pausa novas compras se a perda do dia/semana estourar o limite."""

    enabled: bool = True
    max_daily_loss_pct: float = Field(default=0.03, ge=0.0, le=1.0)
    max_weekly_loss_pct: float = Field(default=0.07, ge=0.0, le=1.0)
    block_new_entries: bool = True
    allow_exits: bool = True
    # Se o halt foi por perda diária, libera automaticamente no próximo dia UTC
    auto_resume_next_day: bool = True


class AppConfig(BaseModel):
    mode: str = "paper"
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    exits: ExitsConfig = Field(default_factory=ExitsConfig)
    capital: CapitalConfig = Field(default_factory=CapitalConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    financeiros_env: str = "paper"
    financeiros_config: str = "config/default.yaml"
    binance_api_key: str = ""
    binance_api_secret: str = ""


def load_config(path: str | Path | None = None) -> AppConfig:
    settings = Settings()
    config_path = Path(path or settings.financeiros_config)
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de config não encontrado: {config_path}")
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(raw)
    # Env pode forçar o modo (paper/live)
    if settings.financeiros_env:
        config.mode = settings.financeiros_env
    return config
