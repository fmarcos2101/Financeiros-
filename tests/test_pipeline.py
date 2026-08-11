from datetime import datetime, timezone
from pathlib import Path

from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.config import AppConfig
from financeiros.data.market import MarketDataService
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.execution.paper import PaperBroker
from financeiros.memory.store import MemoryStore
from financeiros.pipeline import TradingPipeline


class FakeClient(BinancePublicClient):
    def fetch_klines(self, symbol: str, interval: str, limit: int = 120):
        # Gera candles sintéticos estáveis
        from financeiros.models import Candle

        candles = []
        price = 100.0
        base = datetime(2024, 6, 1, tzinfo=timezone.utc)
        for i in range(limit):
            price = 100 + i * 0.1
            ts = base.replace(day=1 + (i % 27), hour=i % 24)
            candles.append(
                Candle(
                    symbol=symbol,
                    open_time=ts,
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    volume=50,
                    close_time=ts,
                )
            )
        return candles

    def fetch_price(self, symbol: str) -> float:
        return 110.0


def test_pipeline_run_once_paper(tmp_path: Path):
    config = AppConfig()
    config.mode = "paper"
    config.universe.symbols = ["BTCUSDT"]
    config.universe.lookback_candles = 80
    config.memory.db_path = str(tmp_path / "mem.db")
    config.analysis.min_signal_strength = 0.5  # força HOLD na maioria dos casos

    memory = MemoryStore(config.memory.db_path)
    portfolio = memory.load_portfolio(config.capital.starting_cash_usdt)
    pipeline = TradingPipeline(
        config=config,
        market=MarketDataService(FakeClient()),
        signals=SignalEngine(config.analysis),
        risk=RiskEngine(config.analysis, config.capital),
        portfolio=portfolio,
        memory=memory,
        broker=PaperBroker(config.execution),
    )
    result = pipeline.run_once()
    assert len(result.decisions) == 1
    assert result.equity_usdt > 0
    assert result.decisions[0].symbol == "BTCUSDT"
    assert result.cycle_id >= 1
    assert "halted" in result.circuit

    # Segundo ciclo reutiliza estado persistido
    memory2 = MemoryStore(config.memory.db_path)
    portfolio2 = memory2.load_portfolio(config.capital.starting_cash_usdt)
    assert portfolio2.cash_usdt == result.cash_usdt
    status = memory2.get_status()
    assert status["last_cycle"]["id"] == result.cycle_id
