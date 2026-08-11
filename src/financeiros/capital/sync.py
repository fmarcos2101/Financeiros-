from __future__ import annotations

from financeiros.capital.portfolio import Portfolio
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.data.providers.binance_trading import BinanceTradingClient
from financeiros.models import Position


def base_asset_from_symbol(symbol: str) -> str:
    """BTCUSDT -> BTC (universo atual é *USDT)."""
    s = symbol.upper()
    if s.endswith("USDT"):
        return s[:-4]
    if s.endswith("BUSD"):
        return s[:-4]
    raise ValueError(f"Não sei extrair base asset de {symbol}")


def sync_portfolio_from_exchange(
    portfolio: Portfolio,
    trading: BinanceTradingClient,
    market: BinancePublicClient,
    symbols: list[str],
    *,
    keep_reserve: bool = True,
    dust_qty: float = 1e-8,
) -> dict:
    """Alinha caixa/posições locais ao saldo free da exchange.

    - cash_usdt ← USDT free na conta
    - posições ← qty free do base asset de cada symbol do universo
    - reserve_usdt continua local (não existe na Binance); mantida se keep_reserve
    - avg_price: preserva se já havia posição; senão usa mark price
    """
    balances = trading.free_balances()
    usdt_free = float(balances.get("USDT") or 0.0)

    before = {
        "cash_usdt": portfolio.cash_usdt,
        "reserve_usdt": portfolio.reserve_usdt,
        "positions": {
            s: {"quantity": p.quantity, "avg_price": p.avg_price}
            for s, p in portfolio.positions.items()
        },
    }

    new_positions: dict[str, Position] = {}
    details: list[dict] = []
    for symbol in symbols:
        symbol_u = symbol.upper()
        base = base_asset_from_symbol(symbol_u)
        qty = float(balances.get(base) or 0.0)
        if qty <= dust_qty:
            details.append(
                {
                    "symbol": symbol_u,
                    "base": base,
                    "quantity": 0.0,
                    "action": "cleared" if symbol_u in portfolio.positions else "absent",
                }
            )
            continue

        mark = float(market.fetch_price(symbol_u))
        old = portfolio.positions.get(symbol_u)
        if old and old.quantity > dust_qty:
            avg = float(old.avg_price)
            peak = max(float(old.peak_price or avg), mark, avg)
            avg_source = "preserved"
        else:
            avg = mark
            peak = mark
            avg_source = "mark_price"

        new_positions[symbol_u] = Position(
            symbol=symbol_u,
            quantity=qty,
            avg_price=avg,
            peak_price=peak,
        )
        details.append(
            {
                "symbol": symbol_u,
                "base": base,
                "quantity": qty,
                "avg_price": avg,
                "mark_price": mark,
                "avg_source": avg_source,
                "action": "synced",
            }
        )

    tracked_bases = {base_asset_from_symbol(s) for s in symbols} | {"USDT"}
    ignored = {
        asset: qty
        for asset, qty in balances.items()
        if asset not in tracked_bases and qty > dust_qty
    }

    portfolio.cash_usdt = usdt_free
    if not keep_reserve:
        portfolio.reserve_usdt = 0.0
    portfolio.positions = new_positions

    after = {
        "cash_usdt": portfolio.cash_usdt,
        "reserve_usdt": portfolio.reserve_usdt,
        "positions": {
            s: {"quantity": p.quantity, "avg_price": p.avg_price}
            for s, p in portfolio.positions.items()
        },
    }
    return {
        "before": before,
        "after": after,
        "details": details,
        "ignored_balances": ignored,
        "keep_reserve": keep_reserve,
    }
