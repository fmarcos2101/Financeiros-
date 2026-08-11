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


def _rsi(closes: list[float], period: int) -> float | None:
    if period <= 0 or len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class SignalEngine:
    """Cruzamento de SMAs com filtros de tendência, momentum (RSI) e volume."""

    def __init__(self, config: AnalysisConfig):
        self.config = config

    def _min_history(self) -> int:
        return max(
            self.config.slow_sma + 2,
            self.config.rsi_period + 2,
            self.config.volume_ma_period + 1,
            self.config.slope_lookback + self.config.slow_sma + 1,
        )

    def evaluate(self, candles: list[Candle]) -> Signal:
        if len(candles) < self._min_history():
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
        volumes = [c.volume for c in candles]
        symbol = candles[-1].symbol
        price = closes[-1]
        fast = _sma(closes, self.config.fast_sma)
        slow = _sma(closes, self.config.slow_sma)
        prev_fast = _sma(closes[:-1], self.config.fast_sma)
        prev_slow = _sma(closes[:-1], self.config.slow_sma)
        vol = _returns_volatility(closes)
        rsi = _rsi(closes, self.config.rsi_period)
        vol_ma = _sma(volumes, self.config.volume_ma_period)
        last_volume = volumes[-1]

        assert fast is not None and slow is not None and prev_fast is not None and prev_slow is not None

        spread = (fast - slow) / price if price else 0.0
        threshold = max(self.config.min_signal_strength, 1e-9)
        strength = min(abs(spread) / threshold, 1.0)

        slow_prev = _sma(closes[: -self.config.slope_lookback], self.config.slow_sma)
        slow_rising = slow_prev is not None and slow > slow_prev
        slow_falling = slow_prev is not None and slow < slow_prev

        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow

        meta = {
            "fast_sma": round(fast, 6),
            "slow_sma": round(slow, 6),
            "spread": round(spread, 6),
            "rsi": None if rsi is None else round(rsi, 4),
            "volume": round(last_volume, 6),
            "volume_ma": None if vol_ma is None else round(vol_ma, 6),
            "slow_rising": slow_rising,
            "filters_failed": [],
        }

        side = Side.HOLD
        rationale = "Sem cruzamento relevante; manter posição/caixa."

        if crossed_up and abs(spread) >= threshold:
            failed: list[str] = []
            if self.config.require_trend_filter and price <= slow:
                failed.append("preço abaixo da SMA lenta")
            if self.config.require_slow_slope and not slow_rising:
                failed.append("SMA lenta sem inclinação de alta")
            if self.config.require_momentum and rsi is not None:
                if rsi < self.config.rsi_buy_min:
                    failed.append(f"RSI baixo ({rsi:.1f} < {self.config.rsi_buy_min})")
                elif rsi > self.config.rsi_buy_max:
                    failed.append(f"RSI esticado ({rsi:.1f} > {self.config.rsi_buy_max})")
            if self.config.require_volume and vol_ma and vol_ma > 0:
                if last_volume < vol_ma * self.config.volume_factor:
                    failed.append(
                        f"volume fraco ({last_volume:.2f} < {vol_ma * self.config.volume_factor:.2f})"
                    )

            meta["filters_failed"] = failed
            if not failed:
                side = Side.BUY
                rationale = (
                    f"Cruzamento de alta confirmado: SMA{self.config.fast_sma} ({fast:.4f}) "
                    f"> SMA{self.config.slow_sma} ({slow:.4f}); "
                    f"RSI={rsi:.1f}; volume ok."
                )
            else:
                rationale = "Cruzamento de alta ignorado: " + "; ".join(failed) + "."
                strength = min(strength, 0.35)

        elif crossed_down and abs(spread) >= threshold:
            failed = []
            if self.config.require_trend_filter and price >= slow:
                failed.append("preço ainda acima da SMA lenta")
            if self.config.require_slow_slope and not slow_falling:
                failed.append("SMA lenta sem inclinação de baixa")
            if self.config.require_momentum and rsi is not None:
                if rsi > self.config.rsi_sell_max:
                    failed.append(f"RSI ainda alto ({rsi:.1f} > {self.config.rsi_sell_max})")
            if self.config.require_volume and vol_ma and vol_ma > 0:
                if last_volume < vol_ma * self.config.volume_factor:
                    failed.append("volume fraco na virada de baixa")

            meta["filters_failed"] = failed
            if not failed:
                side = Side.SELL
                rationale = (
                    f"Cruzamento de baixa confirmado: SMA{self.config.fast_sma} ({fast:.4f}) "
                    f"< SMA{self.config.slow_sma} ({slow:.4f}); "
                    f"RSI={rsi:.1f}."
                )
            else:
                rationale = "Cruzamento de baixa ignorado: " + "; ".join(failed) + "."
                strength = min(strength, 0.35)

        elif abs(spread) >= threshold:
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
            metadata=meta,
        )
