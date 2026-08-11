from __future__ import annotations

from financeiros.config import ExecutionConfig
from financeiros.models import OrderFill, Side, utc_now


class PaperBroker:
    """Simula fills a preço de mercado com taxa configurável."""

    def __init__(self, config: ExecutionConfig):
        self.config = config

    def execute(self, symbol: str, side: Side, quantity: float, price: float) -> OrderFill:
        if side not in (Side.BUY, Side.SELL):
            raise ValueError("PaperBroker só executa BUY/SELL.")
        if quantity <= 0 or price <= 0:
            raise ValueError("Quantity e price devem ser positivos.")

        notional = quantity * price
        fee = notional * (self.config.fee_bps / 10_000.0)
        return OrderFill(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            fee=round(fee, 8),
            notional=round(notional, 8),
            filled_at=utc_now(),
            paper=True,
        )
