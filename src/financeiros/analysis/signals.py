from __future__ import annotations

from financeiros.config import AnalysisConfig
from financeiros.models import Candle, Side, Signal


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window or window <= 0:
        return None
    chunk = values[-window:]
    return sum(chunk) / window


def _returns_volatility(closes: list[float], window: int = 24) -> float:
    if len(closes) < window + 1:
        window = max(len(closes) - 1, 1)
    rets: list[float] = []
    start = max(1, len(closes) - window)
    for i in range(start, len(closes)):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        rets.append((closes[i] - prev) / prev)
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return var**0.5


class SignalEngine:
    """Sinal simples: cruzamento de médias móveis + força relativa ao spread."""

    def __init__(self, config: AnalysisConfig):
        self.config = config

    def evaluate(self, candles: list[Candle]) -> Signal:
        if len(candles) < self.config.slow_sma + 2:
            last = candles[-1] if candles else None
            return Signal(
                symbol=last.symbol if last else "UNKNOWN",
                side=Side.HOLD,
                strength=0.0,
                rationale="Histórico insuficiente para gerar sinal.",
                price=last.close if last else 0.0,
                volatility=0.0,
            )

        closes = [c.close for c in candles]
        symbol = candles[-1].symbol
        price = closes[-1]
        fast = _sma(closes, self.config.fast_sma)
        slow = _sma(closes, self.config.slow_sma)
        prev_fast = _sma(closes[:-1], self.config.fast_sma)
        prev_slow = _sma(closes[:-1], self.config.slow_sma)
        vol = _returns_volatility(closes)

        assert fast is not None and slow is not None and prev_fast is not None and prev_slow is not None

        spread = (fast - slow) / price if price else 0.0
        strength = min(abs(spread) / max(self.config.min_signal_strength, 1e-9), 1.0)

        side = Side.HOLD
        rationale = "Sem cruzamento relevante; manter posição/caixa."

        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow

        if crossed_up and strength >= self.config.min_signal_strength:
            side = Side.BUY
            rationale = (
                f"Cruzamento de alta: SMA{self.config.fast_sma} ({fast:.4f}) "
                f"> SMA{self.config.slow_sma} ({slow:.4f})."
            )
        elif crossed_down and strength >= self.config.min_signal_strength:
            side = Side.SELL
            rationale = (
                f"Cruzamento de baixa: SMA{self.config.fast_sma} ({fast:.4f}) "
                f"< SMA{self.config.slow_sma} ({slow:.4f})."
            )
        elif abs(spread) >= self.config.min_signal_strength:
            # Tendência já estabelecida, mas sem cruzamento fresco → hold
            rationale = (
                f"Tendência presente (spread={spread:.4f}), sem novo cruzamento. "
                "Evita chasing."
            )
            strength = min(strength, 0.4)

        return Signal(
            symbol=symbol,
            side=side,
            strength=round(strength, 4),
            rationale=rationale,
            price=price,
            volatility=round(vol, 6),
            metadata={
                "fast_sma": round(fast, 6),
                "slow_sma": round(slow, 6),
                "spread": round(spread, 6),
            },
        )
