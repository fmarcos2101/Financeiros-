from __future__ import annotations

import hashlib
import hmac
import math
import time
from typing import Any
from urllib.parse import urlencode

import httpx


TESTNET_TRADING_URL = "https://testnet.binance.vision"
LIVE_TRADING_URL = "https://api.binance.com"


class BinanceTradingClient:
    """Cliente assinado para Spot (testnet ou live). Sem withdraw."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = TESTNET_TRADING_URL,
        timeout_seconds: float = 15.0,
        recv_window_ms: int = 5000,
    ):
        if not api_key or not api_secret:
            raise ValueError("API key e secret são obrigatórios para trading.")
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.recv_window_ms = recv_window_ms
        self._filters: dict[str, dict[str, float]] = {}

    def _sign(self, query: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self) -> dict[str, str]:
        return {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "financeiros/0.1",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
    ) -> Any:
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window_ms
            query = urlencode(params, True)
            signature = self._sign(query)
            url = f"{self.base_url}{path}?{query}&signature={signature}"
            body_params = None
        else:
            url = f"{self.base_url}{path}"
            body_params = params or None

        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            response = client.request(method, url, params=None if signed else body_params)
            if response.status_code >= 400:
                detail = response.text
                raise RuntimeError(
                    f"Binance trading HTTP {response.status_code}: {detail}"
                )
            return response.json()

    def ping(self) -> bool:
        self._request("GET", "/api/v3/ping")
        return True

    def account(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/v3/account", signed=True)
        if not isinstance(payload, dict):
            raise RuntimeError("Resposta inesperada de /api/v3/account")
        return payload

    def exchange_info(self, symbol: str) -> dict[str, Any]:
        payload = self._request(
            "GET",
            "/api/v3/exchangeInfo",
            {"symbol": symbol.upper()},
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Resposta inesperada de exchangeInfo")
        return payload

    def load_symbol_filters(self, symbol: str) -> dict[str, float]:
        symbol_u = symbol.upper()
        if symbol_u in self._filters:
            return self._filters[symbol_u]

        info = self.exchange_info(symbol_u)
        symbols = info.get("symbols") or []
        if not symbols:
            raise RuntimeError(f"Symbol {symbol_u} não encontrado na exchange.")
        filters = {f["filterType"]: f for f in symbols[0].get("filters", [])}
        lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
        min_notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        parsed = {
            "step_size": float(lot.get("stepSize") or 0.00001),
            "min_qty": float(lot.get("minQty") or 0.0),
            "min_notional": float(
                min_notional.get("minNotional") or min_notional.get("notional") or 0.0
            ),
        }
        self._filters[symbol_u] = parsed
        return parsed

    @staticmethod
    def round_step(quantity: float, step: float) -> float:
        if step <= 0:
            return quantity
        precision = max(0, int(round(-math.log10(step)))) if step < 1 else 0
        # Evita erro de ponto flutuante (ex.: 0.01/0.00001 → 999.999…)
        floored = math.floor((quantity / step) + 1e-12) * step
        return float(f"{floored:.{precision}f}")

    def normalize_quantity(self, symbol: str, quantity: float, price: float) -> float:
        filters = self.load_symbol_filters(symbol)
        qty = self.round_step(quantity, filters["step_size"])
        if qty < filters["min_qty"]:
            return 0.0
        if filters["min_notional"] and qty * price < filters["min_notional"]:
            return 0.0
        return qty

    def create_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
    ) -> dict[str, Any]:
        side_u = side.upper()
        if side_u not in {"BUY", "SELL"}:
            raise ValueError("side deve ser BUY ou SELL")
        payload = self._request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol.upper(),
                "side": side_u,
                "type": "MARKET",
                "quantity": f"{quantity:.8f}".rstrip("0").rstrip("."),
                "newOrderRespType": "FULL",
            },
            signed=True,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Resposta inesperada de create order")
        return payload

    def free_balances(self) -> dict[str, float]:
        acct = self.account()
        out: dict[str, float] = {}
        for bal in acct.get("balances", []):
            free = float(bal.get("free") or 0.0)
            if free > 0:
                out[str(bal["asset"])] = free
        return out
