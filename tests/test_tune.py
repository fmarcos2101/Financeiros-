from datetime import datetime, timedelta, timezone

from financeiros.config import AppConfig
from financeiros.models import Candle
from financeiros.tune import StrategyTuner


def _hist(symbol: str = "BTCUSDT", n: int = 120) -> list[Candle]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    out = []
    for i in range(n):
        if i < 30:
            price -= 0.4
        elif i < 80:
            price += 0.9
        else:
            price -= 1.2
        ts = base + timedelta(hours=i)
        out.append(
            Candle(
                symbol=symbol,
                open_time=ts,
                open=price,
                high=price * 1.02,
                low=price * 0.97,
                close=price,
                volume=10,
                close_time=ts,
            )
        )
    return out


def test_tuner_ranks_candidates_on_synthetic_history():
    config = AppConfig()
    config.universe.symbols = ["BTCUSDT"]
    config.analysis.min_signal_strength = 0.0001
    config.analysis.max_volatility = 1.0
    config.capital.risk_per_trade = 0.2
    config.capital.max_position_pct = 0.5
    config.capital.min_notional_usdt = 1.0
    config.execution.fee_bps = 0

    tuner = StrategyTuner(config)
    result = tuner.run(
        days=10,
        symbols=["BTCUSDT"],
        interval="1h",
        history={"BTCUSDT": _hist()},
        top_n=3,
    )
    assert result["tested"] > 0
    assert result["best"] is not None
    assert len(result["top"]) >= 1
    assert "score" in result["best"]
