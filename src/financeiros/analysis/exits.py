from __future__ import annotations

from dataclasses import dataclass

from financeiros.config import ExitsConfig
from financeiros.models import Candle, Position, Side


@dataclass
class ExitAdvice:
    symbol: str
    side: Side
    reason: str  # stop_loss | trailing_stop | take_profit
    trigger_price: float
    fill_price: float
    quantity: float
    pnl_pct: float
    rationale: str


class ExitEngine:
    """Regras de saída: stop fixo, trailing stop e take-profit."""

    def __init__(self, config: ExitsConfig):
        self.config = config

    def evaluate(self, position: Position, candles: list[Candle]) -> ExitAdvice | None:
        if not self.config.enabled or position.quantity <= 0 or not candles:
            return None
        if position.avg_price <= 0:
            return None

        last = candles[-1]
        avg = position.avg_price

        # Atualiza pico para trailing (mutável na posição)
        peak = position.peak_price or avg
        peak = max(peak, last.high, last.close, avg)
        position.peak_price = peak

        hard_stop = avg * (1.0 - self.config.stop_loss_pct)
        take_price = avg * (1.0 + self.config.take_profit_pct)

        trailing_armed = False
        trailing_stop = hard_stop
        if self.config.trailing_enabled and self.config.trailing_pct > 0:
            gain_from_entry = (peak - avg) / avg
            if gain_from_entry >= self.config.trailing_activation_pct:
                trailing_armed = True
                trailing_stop = peak * (1.0 - self.config.trailing_pct)

        # Stop efetivo: o mais alto entre hard stop e trailing (protege mais lucro)
        effective_stop = max(hard_stop, trailing_stop) if trailing_armed else hard_stop
        stop_reason = "trailing_stop" if trailing_armed and trailing_stop >= hard_stop else "stop_loss"

        hit_stop = last.low <= effective_stop or last.close <= effective_stop
        hit_take = last.high >= take_price or last.close >= take_price

        # Conservador: se stop e TP no mesmo candle, assume stop.
        if hit_stop:
            fill = min(last.close, effective_stop)
            pnl_pct = (fill - avg) / avg
            label = "Trailing stop" if stop_reason == "trailing_stop" else "Stop-loss"
            return ExitAdvice(
                symbol=position.symbol,
                side=Side.SELL,
                reason=stop_reason,
                trigger_price=round(effective_stop, 8),
                fill_price=round(fill, 8),
                quantity=position.quantity,
                pnl_pct=round(pnl_pct, 6),
                rationale=(
                    f"{label} atingido: preço {fill:.4f} <= stop {effective_stop:.4f} "
                    f"(entrada {avg:.4f}, pico {peak:.4f})."
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
