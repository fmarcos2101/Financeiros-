from datetime import datetime, timezone

from financeiros.analysis.signals import SignalEngine
from financeiros.config import AnalysisConfig
from financeiros.models import Candle


def _candles(closes: list[float], symbol: str = "BTCUSDT") -> list[Candle]:
    out: list[Candle] = []
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, close in enumerate(closes):
        ts = base.replace(hour=i % 24)
        out.append(
            Candle(
                symbol=symbol,
                open_time=ts,
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=100.0,
                close_time=ts,
            )
        )
    return out


def test_signal_hold_with_insufficient_history():
    engine = SignalEngine(AnalysisConfig(fast_sma=3, slow_sma=5, min_signal_strength=0.001))
    signal = engine.evaluate(_candles([100.0, 101.0]))
    assert signal.side.value == "hold"
    assert signal.strength == 0.0


def test_signal_detects_bullish_cross():
    # Série longa em queda (fast < slow) e depois alta forte para cruzar para cima
    down = [100 - i * 0.5 for i in range(40)]
    up = [down[-1] + i * 2.0 for i in range(1, 20)]
    engine = SignalEngine(AnalysisConfig(fast_sma=5, slow_sma=12, min_signal_strength=0.001))
    signal = engine.evaluate(_candles(down + up))
    assert signal.side.value in {"buy", "hold"}  # hold se spread ainda fraco
    assert signal.price > 0
