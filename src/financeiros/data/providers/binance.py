from __future__ import annotations

from datetime import datetime, timezone

import httpx

from financeiros.models import Candle


class BinancePublicClient:
    """Cliente somente leitura da API pública da Binance."""

    def __init__(
        self,
        base_url: str = "https://data-api.binance.vision",
        timeout_seconds: float = 15.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def _get_json(self, path: str, params: dict) -> object:
        url = f"{self.base_url}{path}"
        headers = {"User-Agent": "financeiros/0.1"}
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    def fetch_klines(self, symbol: str, interval: str, limit: int = 120) -> list[Candle]:
        rows = self._get_json(
            "/api/v3/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        if not isinstance(rows, list):
            raise ValueError("Resposta inesperada da API de klines.")

        candles: list[Candle] = []
        for row in rows:
            candles.append(
                Candle(
                    symbol=symbol.upper(),
                    open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    close_time=datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc),
                )
            )
        return candles

    def fetch_price(self, symbol: str) -> float:
        payload = self._get_json("/api/v3/ticker/price", {"symbol": symbol.upper()})
        if not isinstance(payload, dict) or "price" not in payload:
            raise ValueError("Resposta inesperada da API de preço.")
        return float(payload["price"])
