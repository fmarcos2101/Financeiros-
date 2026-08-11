from datetime import datetime, timedelta, timezone

from financeiros.backtest import BacktestEngine
from financeiros.config import AppConfig
from financeiros.models import Candle
from financeiros.validate import OutOfSampleValidator, _filter_history_until


def _hist(symbol: str = "BTCUSDT", n: int = 200) -> list[Candle]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    out = []
    for i in range(n):
        if i < 40:
            price -= 0.3
        elif i < 120:
            price += 0.7
        else:
            price -= 0.4
        ts = base + timedelta(hours=i)
        out.append(
            Candle(
                symbol=symbol,
                open_time=ts,
                open=price,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=150 if i % 7 == 0 else 80,
                close_time=ts,
            )
        )
    return out


def test_active_after_limits_trading_window():
    config = AppConfig()
    config.universe.symbols = ["BTCUSDT"]
    config.analysis.require_trend_filter = False
    config.analysis.require_slow_slope = False
    config.analysis.require_momentum = False
    config.analysis.require_volume = False
    config.analysis.min_signal_strength = 0.0001
    config.analysis.max_volatility = 1.0
    config.capital.risk_per_trade = 0.3
    config.capital.max_position_pct = 0.5
    config.capital.min_notional_usdt = 1.0
    config.execution.fee_bps = 0

    history = {"BTCUSDT": _hist(n=180)}
    split = history["BTCUSDT"][120].open_time
    engine = BacktestEngine(config)
    full = engine.run(days=10, symbols=["BTCUSDT"], interval="1h", history=history)
    holdout = engine.run(
        days=5,
        symbols=["BTCUSDT"],
        interval="1h",
        history=history,
        active_after=split,
    )
    assert holdout.bars < full.bars
    assert holdout.bars > 0
    if holdout.trade_log:
        assert all(t.time >= split for t in holdout.trade_log)


def test_oos_validator_on_synthetic_history():
    config = AppConfig()
    config.universe.symbols = ["BTCUSDT"]
    config.analysis.require_trend_filter = False
    config.analysis.require_slow_slope = False
    config.analysis.require_momentum = False
    config.analysis.require_volume = False
    config.analysis.min_signal_strength = 0.0001
    config.analysis.max_volatility = 1.0
    config.capital.risk_per_trade = 0.3
    config.capital.max_position_pct = 0.5
    config.capital.min_notional_usdt = 1.0
    config.execution.fee_bps = 0

    # Janela sintética "recente" o bastante para o split por holdout_days
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candles = []
    price = 100.0
    for i in range(200):
        ts = now - timedelta(hours=199 - i)
        if i < 50:
            price -= 0.2
        elif i < 140:
            price += 0.6
        else:
            price -= 0.3
        candles.append(
            Candle(
                symbol="BTCUSDT",
                open_time=ts,
                open=price,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=120,
                close_time=ts,
            )
        )

    validator = OutOfSampleValidator(config)
    result = validator.run(
        train_days=6,
        holdout_days=2,
        symbols=["BTCUSDT"],
        interval="1h",
        tune=True,
        min_holdout_return_pct=-50.0,
        max_holdout_dd_pct=50.0,
        min_holdout_trades=0,
        max_return_drop_pct=100.0,
        history={"BTCUSDT": candles},
    )
    assert "train" in result and "holdout" in result
    assert result["verdict"] in {"PASS", "FAIL"}
    assert "params_used" in result
    train_only = _filter_history_until({"BTCUSDT": candles}, datetime.fromisoformat(result["split_at"]))
    assert len(train_only["BTCUSDT"]) < len(candles)


def test_walk_forward_on_synthetic_history():
    config = AppConfig()
    config.universe.symbols = ["BTCUSDT"]
    config.analysis.require_trend_filter = False
    config.analysis.require_slow_slope = False
    config.analysis.require_momentum = False
    config.analysis.require_volume = False
    config.analysis.min_signal_strength = 0.0001
    config.analysis.max_volatility = 1.0
    config.capital.risk_per_trade = 0.3
    config.capital.max_position_pct = 0.5
    config.capital.min_notional_usdt = 1.0
    config.execution.fee_bps = 0

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candles = []
    price = 100.0
    # Precisa cobrir train + holdout * folds (~6 + 2*3 = 12d ≈ 288h) — usamos 400h
    for i in range(400):
        ts = now - timedelta(hours=399 - i)
        if i % 80 < 40:
            price += 0.5
        else:
            price -= 0.35
        candles.append(
            Candle(
                symbol="BTCUSDT",
                open_time=ts,
                open=price,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=120,
                close_time=ts,
            )
        )

    validator = OutOfSampleValidator(config)
    result = validator.run_walk_forward(
        folds=3,
        train_days=6,
        holdout_days=2,
        symbols=["BTCUSDT"],
        interval="1h",
        tune=False,
        min_holdout_return_pct=-50.0,
        max_holdout_dd_pct=50.0,
        min_holdout_trades=0,
        max_return_drop_pct=100.0,
        history={"BTCUSDT": candles},
    )
    assert result["mode"] == "walk_forward"
    assert result["folds"] == 3
    assert len(result["fold_results"]) == 3
    assert result["verdict"] in {"PASS", "FAIL"}
    assert "avg_holdout_return_pct" in result
    for fold in result["fold_results"]:
        assert "train" in fold and "holdout" in fold
        assert fold["verdict"] in {"PASS", "FAIL"}
