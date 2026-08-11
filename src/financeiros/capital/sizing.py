from __future__ import annotations

from financeiros.config import CapitalConfig
from financeiros.models import RiskAssessment, Side, Signal


def size_order(signal: Signal, risk: RiskAssessment, capital: CapitalConfig) -> tuple[float, float]:
    """Retorna (quantity, notional) já respeitando os limites do risk engine."""
    if not risk.approved or signal.side == Side.HOLD:
        return 0.0, 0.0
    qty = risk.suggested_qty
    notional = qty * signal.price
    if signal.side == Side.BUY and notional < capital.min_notional_usdt:
        return 0.0, 0.0
    return round(qty, 8), round(notional, 6)
