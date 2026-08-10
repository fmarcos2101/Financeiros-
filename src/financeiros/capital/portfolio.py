from __future__ import annotations

from financeiros.models import OrderFill, PortfolioSnapshot, Position, Side, utc_now


class Portfolio:
    def __init__(self, starting_cash_usdt: float):
        self.cash_usdt = float(starting_cash_usdt)
        self.positions: dict[str, Position] = {}

    def mark_to_market(self, prices: dict[str, float]) -> PortfolioSnapshot:
        equity = self.cash_usdt
        for symbol, pos in self.positions.items():
            px = prices.get(symbol, pos.avg_price)
            equity += pos.quantity * px
        return PortfolioSnapshot(
            cash_usdt=round(self.cash_usdt, 8),
            positions={k: v.model_copy(deep=True) for k, v in self.positions.items()},
            equity_usdt=round(equity, 8),
            updated_at=utc_now(),
        )

    def apply_fill(self, fill: OrderFill) -> None:
        pos = self.positions.get(fill.symbol) or Position(symbol=fill.symbol)

        if fill.side == Side.BUY:
            total_cost = fill.notional + fill.fee
            if total_cost > self.cash_usdt + 1e-9:
                raise ValueError("Caixa insuficiente para aplicar fill de compra.")
            new_qty = pos.quantity + fill.quantity
            if new_qty <= 0:
                raise ValueError("Quantidade inválida após compra.")
            pos.avg_price = ((pos.quantity * pos.avg_price) + fill.notional) / new_qty
            pos.quantity = new_qty
            self.cash_usdt -= total_cost
            self.positions[fill.symbol] = pos
            return

        if fill.side == Side.SELL:
            if fill.quantity > pos.quantity + 1e-12:
                raise ValueError("Tentativa de vender mais do que a posição.")
            proceeds = fill.notional - fill.fee
            pos.quantity -= fill.quantity
            self.cash_usdt += proceeds
            if pos.quantity <= 1e-12:
                self.positions.pop(fill.symbol, None)
            else:
                self.positions[fill.symbol] = pos
            return

        raise ValueError(f"Side não suportado no fill: {fill.side}")
