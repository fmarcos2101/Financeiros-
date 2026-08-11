from datetime import datetime, timezone
from pathlib import Path

from financeiros.analysis.exits import ExitEngine
from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.capital.portfolio import Portfolio
from financeiros.config import AppConfig, ExecutionConfig
from financeiros.data.market import MarketDataService
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.execution.paper import PaperBroker
from financeiros.memory.store import MemoryStore
from financeiros.models import Candle, Side
from financeiros.pipeline import TradingPipeline


class PriceClient(BinancePublicClient):
    def __init__(self, closes: list[float]):
        super().__init__()
        self.closes = closes

    def fetch_klines(self, symbol: str, interval: str, limit: int = 120):
        base = datetime(2024, 6, 1, tzinfo=timezone.utc)
        out = []
        for i, close in enumerate(self.closes[-limit:]):
            # último candle estressa o stop
            if i == len(self.closes[-limit:]) - 1:
                low = close * 0.9
                high = close
            else:
                low = close * 0.99
                high = close * 1.01
            ts = base.replace(day=1 + (i % 27), hour=i % 24)
            out.append(
                Candle(
                    symbol=symbol,
                    open_time=ts,
                    open=close,
                    high=high,
                    low=low,
                    close=close,
                    volume=10,
                    close_time=ts,
                )
            )
        return out

    def fetch_price(self, symbol: str) -> float:
        return self.closes[-1]


def test_pipeline_executes_stop_loss(tmp_path: Path):
    config = AppConfig()
    config.mode = "paper"
    config.universe.symbols = ["BTCUSDT"]
    config.memory.db_path = str(tmp_path / "exit.db")
    config.exits.enabled = True
    config.exits.stop_loss_pct = 0.03
    config.exits.take_profit_pct = 0.10
    config.capital.reserve_enabled = False
    config.execution.fee_bps = 0

    memory = MemoryStore(config.memory.db_path)
    portfolio = Portfolio(1000.0)
    # posição que será stopada no último candle (~90)
    buy = PaperBroker(ExecutionConfig(fee_bps=0)).execute(
        "BTCUSDT", Side.BUY, quantity=1.0, price=100.0
    )
    portfolio.apply_fill(buy)
    memory.save_portfolio(portfolio)

    closes = [100 + i * 0.01 for i in range(80)]
    closes[-1] = 95.0  # close ainda perto, mas low do fake client = 0.9*95

    pipeline = TradingPipeline(
        config=config,
        market=MarketDataService(PriceClient(closes)),
        signals=SignalEngine(config.analysis),
        risk=RiskEngine(config.analysis, config.capital),
        portfolio=portfolio,
        memory=memory,
        broker=PaperBroker(config.execution),
        exits=ExitEngine(config.exits),
    )
    result = pipeline.run_once()
    assert result.exits_this_cycle == 1
    assert result.decisions[0].metadata.get("exit_reason") == "stop_loss"
    assert "BTCUSDT" not in portfolio.positions
    assert result.cash_usdt > 900
