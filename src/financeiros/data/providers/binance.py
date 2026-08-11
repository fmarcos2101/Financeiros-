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

    def _rows_to_candles(self, symbol: str, rows: list) -> list[Candle]:
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

    def fetch_klines(self, symbol: str, interval: str, limit: int = 120) -> list[Candle]:
        rows = self._get_json(
            "/api/v3/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        if not isinstance(rows, list):
            raise ValueError("Resposta inesperada da API de klines.")
        return self._rows_to_candles(symbol, rows)

    def fetch_klines_range(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int | None = None,
        *,
        max_candles: int = 5000,
    ) -> list[Candle]:
        """Busca histórico paginado (até 1000 candles por request)."""
        all_rows: list = []
        cursor = start_ms
        symbol_u = symbol.upper()

        while len(all_rows) < max_candles:
            params: dict = {
                "symbol": symbol_u,
                "interval": interval,
                "startTime": cursor,
                "limit": 1000,
            }
            if end_ms is not None:
                params["endTime"] = end_ms
            rows = self._get_json("/api/v3/klines", params)
            if not isinstance(rows, list) or not rows:
                break
            all_rows.extend(rows)
            last_open = int(rows[-1][0])
            next_cursor = last_open + 1
            if end_ms is not None and last_open >= end_ms:
                break
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(rows) < 1000:
                break

        # Dedup por open_time
        seen: set[int] = set()
        unique: list = []
        for row in all_rows:
            ot = int(row[0])
            if ot in seen:
                continue
            seen.add(ot)
            unique.append(row)
            if len(unique) >= max_candles:
                break
        return self._rows_to_candles(symbol, unique)

    def fetch_price(self, symbol: str) -> float:
        payload = self._get_json("/api/v3/ticker/price", {"symbol": symbol.upper()})
        if not isinstance(payload, dict) or "price" not in payload:
            raise ValueError("Resposta inesperada da API de preço.")
        return float(payload["price"])
