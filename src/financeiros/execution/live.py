from __future__ import annotations

from financeiros.config import ExecutionConfig
from financeiros.data.providers.binance_trading import BinanceTradingClient
from financeiros.models import OrderFill, Side, utc_now


class LiveBroker:
    """Executa MARKET orders na Binance (testnet/live) com guardrails."""

    def __init__(
        self,
        config: ExecutionConfig,
        client: BinanceTradingClient,
        *,
        mode: str = "testnet",
    ):
        if mode not in {"testnet", "live"}:
            raise ValueError("LiveBroker exige mode testnet|live")
        self.config = config
        self.client = client
        self.mode = mode

    def execute(self, symbol: str, side: Side, quantity: float, price: float) -> OrderFill:
        if side not in (Side.BUY, Side.SELL):
            raise ValueError("LiveBroker só executa BUY/SELL.")
        if quantity <= 0 or price <= 0:
            raise ValueError("Quantity e price devem ser positivos.")

        notional = quantity * price
        if notional > self.config.max_order_notional_usdt:
            raise RuntimeError(
                f"Ordem {notional:.2f} USDT excede max_order_notional_usdt="
                f"{self.config.max_order_notional_usdt:.2f}. "
                "Ajuste config ou reduza sizing."
            )

        qty = self.client.normalize_quantity(symbol, quantity, price)
        if qty <= 0:
            raise RuntimeError(
                f"Quantidade {quantity} inválida após filtros LOT_SIZE/MIN_NOTIONAL "
                f"para {symbol}."
            )

        if self.config.dry_run:
            fee = notional * (self.config.fee_bps / 10_000.0)
            return OrderFill(
                symbol=symbol,
                side=side,
                quantity=qty,
                price=price,
                fee=round(fee, 8),
                notional=round(qty * price, 8),
                filled_at=utc_now(),
                paper=True,
            )

        raw = self.client.create_market_order(
            symbol=symbol,
            side=side.value,
            quantity=qty,
        )
        return self._fill_from_order(symbol, side, raw, fallback_price=price)

    def _fill_from_order(
        self,
        symbol: str,
        side: Side,
        raw: dict,
        *,
        fallback_price: float,
    ) -> OrderFill:
        fills = raw.get("fills") or []
        if fills:
            qty = sum(float(f["qty"]) for f in fills)
            notional = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            fee = sum(float(f.get("commission") or 0.0) for f in fills)
            price = notional / qty if qty else fallback_price
        else:
            qty = float(raw.get("executedQty") or 0.0)
            # cummulativeQuoteQty is the Binance spelling
            notional = float(raw.get("cummulativeQuoteQty") or 0.0)
            price = (notional / qty) if qty else fallback_price
            fee = notional * (self.config.fee_bps / 10_000.0)

        if qty <= 0:
            raise RuntimeError(f"Ordem sem fill: {raw}")

        return OrderFill(
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            fee=round(fee, 8),
            notional=round(notional if notional else qty * price, 8),
            filled_at=utc_now(),
            paper=False,
        )
