from __future__ import annotations

from financeiros.data.providers.binance import BinancePublicClient
from financeiros.models import Candle


class MarketDataService:
    def __init__(self, client: BinancePublicClient):
        self.client = client

    def get_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        return self.client.fetch_klines(symbol=symbol, interval=interval, limit=limit)

    def get_price(self, symbol: str) -> float:
        return self.client.fetch_price(symbol)
