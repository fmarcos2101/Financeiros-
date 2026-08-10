from __future__ import annotations

from dataclasses import dataclass

from financeiros.config import ExitsConfig
from financeiros.models import Candle, Position, Side


@dataclass
class ExitAdvice:
    symbol: str
    side: Side
    reason: str  # stop_loss | take_profit
    trigger_price: float
    fill_price: float
    quantity: float
    pnl_pct: float
    rationale: str


class ExitEngine:
    """Regras de saída: stop-loss e take-profit sobre o preço médio de entrada."""

    def __init__(self, config: ExitsConfig):
        self.config = config

    def evaluate(self, position: Position, candles: list[Candle]) -> ExitAdvice | None:
        if not self.config.enabled or position.quantity <= 0 or not candles:
            return None
        if position.avg_price <= 0:
            return None

        last = candles[-1]
        avg = position.avg_price
        stop_price = avg * (1.0 - self.config.stop_loss_pct)
        take_price = avg * (1.0 + self.config.take_profit_pct)

        hit_stop = last.low <= stop_price or last.close <= stop_price
        hit_take = last.high >= take_price or last.close >= take_price

        # Se stop e TP no mesmo candle, assume o pior caso (stop) — mais conservador.
        if hit_stop:
            fill = min(last.close, stop_price)
            pnl_pct = (fill - avg) / avg
            return ExitAdvice(
                symbol=position.symbol,
                side=Side.SELL,
                reason="stop_loss",
                trigger_price=round(stop_price, 8),
                fill_price=round(fill, 8),
                quantity=position.quantity,
                pnl_pct=round(pnl_pct, 6),
                rationale=(
                    f"Stop-loss atingido: preço {fill:.4f} <= stop {stop_price:.4f} "
                    f"(entrada {avg:.4f}, limite -{self.config.stop_loss_pct:.1%})."
                ),
            )

        if hit_take:
            fill = take_price if last.high >= take_price else last.close
            pnl_pct = (fill - avg) / avg
            return ExitAdvice(
                symbol=position.symbol,
                side=Side.SELL,
                reason="take_profit",
                trigger_price=round(take_price, 8),
                fill_price=round(fill, 8),
                quantity=position.quantity,
                pnl_pct=round(pnl_pct, 6),
                rationale=(
                    f"Take-profit atingido: preço {fill:.4f} >= alvo {take_price:.4f} "
                    f"(entrada {avg:.4f}, alvo +{self.config.take_profit_pct:.1%})."
                ),
            )

        return None
