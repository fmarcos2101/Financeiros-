from datetime import datetime, timezone

from financeiros.analysis.exits import ExitEngine
from financeiros.config import ExitsConfig
from financeiros.models import Candle, Position


def _candle(close: float, high: float | None = None, low: float | None = None) -> Candle:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return Candle(
        symbol="BTCUSDT",
        open_time=ts,
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=10,
        close_time=ts,
    )


def test_stop_loss_triggers_on_low():
    engine = ExitEngine(
        ExitsConfig(
            enabled=True,
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            trailing_enabled=False,
        )
    )
    pos = Position(symbol="BTCUSDT", quantity=0.1, avg_price=100.0, peak_price=100.0)
    advice = engine.evaluate(pos, [_candle(close=98.0, high=99.0, low=96.5)])
    assert advice is not None
    assert advice.reason == "stop_loss"
    assert advice.quantity == 0.1
    assert advice.fill_price <= 97.0


def test_take_profit_triggers_on_high():
    engine = ExitEngine(
        ExitsConfig(
            enabled=True,
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            trailing_enabled=False,
        )
    )
    pos = Position(symbol="BTCUSDT", quantity=0.2, avg_price=100.0, peak_price=100.0)
    advice = engine.evaluate(pos, [_candle(close=105.0, high=106.5, low=104.0)])
    assert advice is not None
    assert advice.reason == "take_profit"
    assert advice.fill_price == 106.0


def test_no_exit_inside_band():
    engine = ExitEngine(
        ExitsConfig(
            enabled=True,
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            trailing_enabled=False,
        )
    )
    pos = Position(symbol="ETHUSDT", quantity=1.0, avg_price=200.0, peak_price=200.0)
    advice = engine.evaluate(pos, [_candle(close=202.0, high=204.0, low=198.0)])
    assert advice is None


def test_stop_wins_when_both_hit_same_candle():
    engine = ExitEngine(
        ExitsConfig(
            enabled=True,
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            trailing_enabled=False,
        )
    )
    pos = Position(symbol="BTCUSDT", quantity=1.0, avg_price=100.0, peak_price=100.0)
    advice = engine.evaluate(pos, [_candle(close=100.0, high=110.0, low=90.0)])
    assert advice is not None
    assert advice.reason == "stop_loss"


def test_trailing_stop_activates_after_gain():
    engine = ExitEngine(
        ExitsConfig(
            enabled=True,
            stop_loss_pct=0.05,
            take_profit_pct=0.50,
            trailing_enabled=True,
            trailing_pct=0.02,
            trailing_activation_pct=0.02,
        )
    )
    pos = Position(symbol="BTCUSDT", quantity=1.0, avg_price=100.0, peak_price=100.0)
    # Sobe para 110 (arma trailing; stop trail = 107.8)
    assert engine.evaluate(pos, [_candle(close=110.0, high=110.0, low=109.0)]) is None
    assert pos.peak_price == 110.0
    # Recua abaixo do trailing
    advice = engine.evaluate(pos, [_candle(close=107.0, high=108.0, low=106.0)])
    assert advice is not None
    assert advice.reason == "trailing_stop"
    assert advice.fill_price <= 107.8
