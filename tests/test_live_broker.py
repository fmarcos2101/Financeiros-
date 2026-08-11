import hashlib
import hmac

import httpx
import pytest

from financeiros.config import AppConfig, ExecutionConfig
from financeiros.data.providers.binance_trading import BinanceTradingClient
from financeiros.execution.live import LiveBroker
from financeiros.models import Side


class FakeTransport(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)


def test_sign_matches_hmac():
    client = BinanceTradingClient("k", "secret", base_url="https://example.test")
    query = "symbol=BTCUSDT&side=BUY&type=MARKET&quantity=0.001&timestamp=1"
    expected = hmac.new(b"secret", query.encode(), hashlib.sha256).hexdigest()
    assert client._sign(query) == expected


def test_round_step():
    assert BinanceTradingClient.round_step(0.123456, 0.001) == 0.123
    assert BinanceTradingClient.round_step(1.999, 0.01) == 1.99


def test_live_broker_dry_run_does_not_hit_order_endpoint():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "filters": [
                                {
                                    "filterType": "LOT_SIZE",
                                    "stepSize": "0.00001",
                                    "minQty": "0.00001",
                                },
                                {"filterType": "NOTIONAL", "minNotional": "1"},
                            ],
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request {request.url}")

    transport = FakeTransport(handler)

    class PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import financeiros.data.providers.binance_trading as mod

    original = mod.httpx.Client
    mod.httpx.Client = PatchedClient
    try:
        trading = BinanceTradingClient("k", "s", base_url="https://example.test")
        broker = LiveBroker(
            ExecutionConfig(dry_run=True, max_order_notional_usdt=100, fee_bps=10),
            trading,
            mode="testnet",
        )
        fill = broker.execute("BTCUSDT", Side.BUY, 0.01, 100.0)
        assert fill.paper is True
        assert fill.quantity == 0.01
        assert fill.fee > 0
        assert any(c.startswith("GET /api/v3/exchangeInfo") for c in calls)
        assert not any("/api/v3/order" in c for c in calls)
    finally:
        mod.httpx.Client = original


def test_live_broker_blocks_over_max_notional():
    trading = BinanceTradingClient("k", "s", base_url="https://example.test")
    broker = LiveBroker(
        ExecutionConfig(dry_run=True, max_order_notional_usdt=10),
        trading,
        mode="testnet",
    )
    with pytest.raises(RuntimeError, match="max_order_notional"):
        broker.execute("BTCUSDT", Side.BUY, 1.0, 100.0)


def test_live_broker_real_order_parses_fills(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "filters": [
                                {
                                    "filterType": "LOT_SIZE",
                                    "stepSize": "0.001",
                                    "minQty": "0.001",
                                },
                                {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                            ],
                        }
                    ]
                },
            )
        if request.url.path == "/api/v3/order" and request.method == "POST":
            # Ensure signature present
            assert "signature=" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "executedQty": "0.010",
                    "cummulativeQuoteQty": "100.0",
                    "fills": [
                        {
                            "price": "10000.0",
                            "qty": "0.010",
                            "commission": "0.01",
                            "commissionAsset": "USDT",
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"msg": "nope"})

    transport = FakeTransport(handler)

    class PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import financeiros.data.providers.binance_trading as mod

    monkeypatch.setattr(mod.httpx, "Client", PatchedClient)
    trading = BinanceTradingClient("k", "s", base_url="https://example.test")
    broker = LiveBroker(
        ExecutionConfig(dry_run=False, max_order_notional_usdt=200, fee_bps=10),
        trading,
        mode="live",
    )
    fill = broker.execute("BTCUSDT", Side.BUY, 0.01, 10000.0)
    assert fill.paper is False
    assert fill.quantity == pytest.approx(0.01)
    assert fill.price == pytest.approx(10000.0)
    assert fill.fee == pytest.approx(0.01)


def test_pipeline_allows_testnet_dry_run(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from financeiros.analysis.risk import RiskEngine
    from financeiros.analysis.signals import SignalEngine
    from financeiros.data.market import MarketDataService
    from financeiros.data.providers.binance import BinancePublicClient
    from financeiros.memory.store import MemoryStore
    from financeiros.models import Candle
    from financeiros.pipeline import TradingPipeline

    class FakeMarket(BinancePublicClient):
        def fetch_klines(self, symbol: str, interval: str, limit: int = 120):
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

    config = AppConfig()
    config.mode = "testnet"
    config.execution.dry_run = True
    config.execution.max_order_notional_usdt = 500
    config.universe.symbols = ["BTCUSDT"]
    config.universe.lookback_candles = 80
    config.memory.db_path = str(tmp_path / "mem.db")
    config.analysis.min_signal_strength = 0.5

    trading = BinanceTradingClient("k", "s", base_url="https://example.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "filters": [
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.00001",
                                "minQty": "0.00001",
                            },
                            {"filterType": "NOTIONAL", "minNotional": "1"},
                        ],
                    }
                ]
            },
        )

    class PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = FakeTransport(handler)
            super().__init__(*args, **kwargs)

    import financeiros.data.providers.binance_trading as mod

    monkeypatch.setattr(mod.httpx, "Client", PatchedClient)

    memory = MemoryStore(config.memory.db_path)
    pipeline = TradingPipeline(
        config=config,
        market=MarketDataService(FakeMarket()),
        signals=SignalEngine(config.analysis),
        risk=RiskEngine(config.analysis, config.capital),
        portfolio=memory.load_portfolio(config.capital.starting_cash_usdt),
        memory=memory,
        broker=LiveBroker(config.execution, trading, mode="testnet"),
    )
    result = pipeline.run_once()
    assert result.cycle_id >= 1
    assert any("testnet" in d.tags for d in result.decisions)
