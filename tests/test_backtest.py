from datetime import datetime, timedelta, timezone

from financeiros.backtest import BacktestEngine
from financeiros.config import AppConfig
from financeiros.models import Candle


def _synth_history(symbol: str, n: int = 200, *, crash_at: int | None = 150) -> list[Candle]:
    """Série sintética: sobe, cruza médias, depois pode cair para acionar stop."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles: list[Candle] = []
    price = 100.0
    for i in range(n):
        if i < 40:
            price = 100 - i * 0.3  # queda para depois cruzar pra cima
        elif crash_at is not None and i >= crash_at:
            price = price - 3.0  # queda forte após entrada típica
        elif i < 120:
            price = price + 0.8  # tendência de alta
        else:
            price = price + 0.1

        high = price * 1.01
        if crash_at is not None and i >= crash_at:
            low = price * 0.90  # lows profundos na fase de crash
        else:
            low = price * 0.99

        ts = base + timedelta(hours=i)
        candles.append(
            Candle(
                symbol=symbol,
                open_time=ts,
                open=price,
                high=high,
                low=low,
                close=price,
                volume=100,
                close_time=ts,
            )
        )
    return candles


def test_backtest_runs_on_synthetic_data():
    config = AppConfig()
    config.universe.symbols = ["BTCUSDT"]
    config.analysis.min_signal_strength = 0.001
    config.exits.enabled = True
    config.exits.stop_loss_pct = 0.03
    config.exits.take_profit_pct = 0.20
    config.capital.reserve_enabled = True
    config.capital.reserve_skim_pct = 0.20
    config.execution.fee_bps = 0
    # sizing mais generoso para haver trades no sintético
    config.capital.risk_per_trade = 0.10
    config.capital.max_position_pct = 0.50
    config.analysis.max_volatility = 1.0

    engine = BacktestEngine(config, client=None)  # type: ignore[arg-type]
    history = {"BTCUSDT": _synth_history("BTCUSDT")}
    report = engine.run(days=30, symbols=["BTCUSDT"], interval="1h", history=history)

    assert report.bars > 0
    assert report.trades >= 1
    assert report.ending_wealth_usdt > 0
    assert report.max_drawdown_pct >= 0
    payload = report.to_dict(include_trades=True)
    assert "trade_log" in payload
    assert payload["symbols"] == ["BTCUSDT"]


def test_backtest_metrics_with_forced_roundtrip():
    config = AppConfig()
    config.universe.symbols = ["ETHUSDT"]
    config.analysis.fast_sma = 3
    config.analysis.slow_sma = 5
    config.analysis.min_signal_strength = 0.0001
    config.analysis.max_volatility = 1.0
    config.capital.risk_per_trade = 0.5
    config.capital.max_position_pct = 0.8
    config.capital.min_notional_usdt = 1.0
    config.capital.reserve_enabled = False
    config.exits.enabled = True
    config.exits.stop_loss_pct = 0.02
    config.exits.take_profit_pct = 0.50
    config.execution.fee_bps = 0

    engine = BacktestEngine(config)
    report = engine.run(
        days=10,
        symbols=["ETHUSDT"],
        interval="1h",
        history={"ETHUSDT": _synth_history("ETHUSDT", n=80, crash_at=60)},
    )
    assert report.buys >= 1
    # com crash sintético, espera-se ao menos uma saída por stop ou sell
    assert report.sells >= 1
    assert report.total_return_pct == report.total_return_pct  # not NaN
