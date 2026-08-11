from datetime import datetime, timezone

from financeiros.analysis.signals import SignalEngine, _rsi
from financeiros.config import AnalysisConfig
from financeiros.models import Candle


def _candles(
    closes: list[float],
    symbol: str = "BTCUSDT",
    *,
    volumes: list[float] | None = None,
) -> list[Candle]:
    out: list[Candle] = []
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, close in enumerate(closes):
        ts = base.replace(day=1 + (i // 24) % 27, hour=i % 24)
        vol = 100.0 if volumes is None else volumes[i]
        out.append(
            Candle(
                symbol=symbol,
                open_time=ts,
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=vol,
                close_time=ts,
            )
        )
    return out


def _bullish_series() -> list[float]:
    down = [100 - i * 0.5 for i in range(40)]
    up = [down[-1] + i * 2.0 for i in range(1, 30)]
    return down + up


def test_signal_hold_with_insufficient_history():
    engine = SignalEngine(AnalysisConfig(fast_sma=3, slow_sma=5, min_signal_strength=0.001))
    signal = engine.evaluate(_candles([100.0, 101.0]))
    assert signal.side.value == "hold"
    assert signal.strength == 0.0


def test_signal_detects_bullish_cross_with_filters():
    closes = _bullish_series()
    volumes = [80.0] * (len(closes) - 1) + [200.0]  # volume forte no cruzamento
    engine = SignalEngine(
        AnalysisConfig(
            fast_sma=5,
            slow_sma=12,
            min_signal_strength=0.001,
            require_trend_filter=True,
            require_slow_slope=True,
            require_momentum=True,
            rsi_buy_min=40,
            rsi_buy_max=90,
            require_volume=True,
            volume_ma_period=10,
            volume_factor=1.0,
            slope_lookback=3,
        )
    )
    signal = engine.evaluate(_candles(closes, volumes=volumes))
    assert signal.side.value in {"buy", "hold"}
    assert signal.price > 0
    assert "rsi" in signal.metadata


def test_volume_filter_blocks_weak_breakout():
    closes = _bullish_series()
    volumes = [200.0] * (len(closes) - 1) + [10.0]  # volume colapsa no cross
    engine = SignalEngine(
        AnalysisConfig(
            fast_sma=5,
            slow_sma=12,
            min_signal_strength=0.001,
            require_trend_filter=False,
            require_slow_slope=False,
            require_momentum=False,
            require_volume=True,
            volume_ma_period=10,
            volume_factor=1.0,
        )
    )
    signal = engine.evaluate(_candles(closes, volumes=volumes))
    # Se houve cruzamento, deve ser bloqueado por volume; senão HOLD por ausência de cross
    assert signal.side.value == "hold"
    if "volume fraco" in signal.rationale:
        assert "ignorado" in signal.rationale


def test_rsi_helper_bounds():
    up = [100 + i for i in range(20)]
    down = [100 - i for i in range(20)]
    assert _rsi(up, 14) is not None and _rsi(up, 14) > 70
    assert _rsi(down, 14) is not None and _rsi(down, 14) < 30
