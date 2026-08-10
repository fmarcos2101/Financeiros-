from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Candle(BaseModel):
    symbol: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: datetime


class Signal(BaseModel):
    symbol: str
    side: Side
    strength: float = Field(ge=0.0, le=1.0)
    rationale: str
    price: float
    volatility: float
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    symbol: str
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    max_notional: float = 0.0
    suggested_qty: float = 0.0


class Decision(BaseModel):
    symbol: str
    side: Side
    approved: bool
    rationale: str
    signal_strength: float
    price: float
    quantity: float = 0.0
    notional: float = 0.0
    created_at: datetime = Field(default_factory=utc_now)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderFill(BaseModel):
    symbol: str
    side: Side
    quantity: float
    price: float
    fee: float
    notional: float
    filled_at: datetime = Field(default_factory=utc_now)
    paper: bool = True


class Position(BaseModel):
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0

    @property
    def notional(self) -> float:
        return self.quantity * self.avg_price


class PortfolioSnapshot(BaseModel):
    cash_usdt: float
    reserve_usdt: float = 0.0
    positions: dict[str, Position] = Field(default_factory=dict)
    equity_usdt: float = 0.0  # caixa de trading + posições (sem reserva)
    total_wealth_usdt: float = 0.0  # equity + reserva
    updated_at: datetime = Field(default_factory=utc_now)
