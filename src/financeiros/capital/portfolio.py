from __future__ import annotations

from dataclasses import dataclass

from financeiros.models import OrderFill, PortfolioSnapshot, Position, Side, utc_now


@dataclass
class FillOutcome:
    realized_pnl: float = 0.0
    reserve_skim: float = 0.0


class Portfolio:
    """Caixa de trading + posições + fundo reserva (não usado para operar)."""

    def __init__(self, starting_cash_usdt: float, reserve_usdt: float = 0.0):
        self.cash_usdt = float(starting_cash_usdt)
        self.reserve_usdt = float(reserve_usdt)
        self.positions: dict[str, Position] = {}

    @property
    def total_wealth_usdt(self) -> float:
        # wealth sem mark-to-market das posições
        return self.cash_usdt + self.reserve_usdt + sum(p.notional for p in self.positions.values())

    def mark_to_market(self, prices: dict[str, float]) -> PortfolioSnapshot:
        # equity de trading: reserva fica de fora do risco operacional
        equity = self.cash_usdt
        for symbol, pos in self.positions.items():
            px = prices.get(symbol, pos.avg_price)
            equity += pos.quantity * px
        return PortfolioSnapshot(
            cash_usdt=round(self.cash_usdt, 8),
            reserve_usdt=round(self.reserve_usdt, 8),
            positions={k: v.model_copy(deep=True) for k, v in self.positions.items()},
            equity_usdt=round(equity, 8),
            total_wealth_usdt=round(equity + self.reserve_usdt, 8),
            updated_at=utc_now(),
        )

    def apply_fill(self, fill: OrderFill, reserve_skim_pct: float = 0.0) -> FillOutcome:
        pos = self.positions.get(fill.symbol) or Position(symbol=fill.symbol)
        outcome = FillOutcome()

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
            return outcome

        if fill.side == Side.SELL:
            if fill.quantity > pos.quantity + 1e-12:
                raise ValueError("Tentativa de vender mais do que a posição.")

            cost_basis = pos.avg_price * fill.quantity
            proceeds = fill.notional - fill.fee
            realized = proceeds - cost_basis
            outcome.realized_pnl = round(realized, 8)

            pos.quantity -= fill.quantity
            self.cash_usdt += proceeds
            if pos.quantity <= 1e-12:
                self.positions.pop(fill.symbol, None)
            else:
                self.positions[fill.symbol] = pos

            if realized > 0 and reserve_skim_pct > 0:
                skim = min(realized * reserve_skim_pct, self.cash_usdt)
                skim = round(max(skim, 0.0), 8)
                if skim > 0:
                    self.cash_usdt -= skim
                    self.reserve_usdt += skim
                    outcome.reserve_skim = skim
            return outcome

        raise ValueError(f"Side não suportado no fill: {fill.side}")
