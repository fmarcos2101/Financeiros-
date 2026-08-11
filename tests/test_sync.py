from financeiros.capital.portfolio import Portfolio
from financeiros.capital.sync import base_asset_from_symbol, sync_portfolio_from_exchange
from financeiros.models import Position


class FakeTrading:
    def __init__(self, balances: dict[str, float]):
        self._balances = balances

    def free_balances(self) -> dict[str, float]:
        return dict(self._balances)


class FakeMarket:
    def __init__(self, prices: dict[str, float]):
        self._prices = prices

    def fetch_price(self, symbol: str) -> float:
        return self._prices[symbol]


def test_base_asset_from_symbol():
    assert base_asset_from_symbol("BTCUSDT") == "BTC"
    assert base_asset_from_symbol("ethusdt") == "ETH"


def test_sync_overwrites_cash_and_positions():
    portfolio = Portfolio(1000.0, reserve_usdt=50.0)
    portfolio.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", quantity=0.01, avg_price=20000.0, peak_price=21000.0
    )

    report = sync_portfolio_from_exchange(
        portfolio,
        FakeTrading({"USDT": 250.0, "ETH": 0.5, "BNB": 1.0}),
        FakeMarket({"BTCUSDT": 30000.0, "ETHUSDT": 2000.0}),
        ["BTCUSDT", "ETHUSDT"],
        keep_reserve=True,
    )

    assert portfolio.cash_usdt == 250.0
    assert portfolio.reserve_usdt == 50.0  # preservada
    assert "BTCUSDT" not in portfolio.positions
    assert portfolio.positions["ETHUSDT"].quantity == 0.5
    assert portfolio.positions["ETHUSDT"].avg_price == 2000.0  # mark (nova)
    assert report["ignored_balances"]["BNB"] == 1.0
    assert report["before"]["cash_usdt"] == 1000.0


def test_sync_preserves_avg_price_for_existing_position():
    portfolio = Portfolio(100.0)
    portfolio.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT", quantity=0.02, avg_price=25000.0, peak_price=26000.0
    )
    sync_portfolio_from_exchange(
        portfolio,
        FakeTrading({"USDT": 80.0, "BTC": 0.015}),
        FakeMarket({"BTCUSDT": 30000.0}),
        ["BTCUSDT"],
    )
    assert portfolio.positions["BTCUSDT"].quantity == 0.015
    assert portfolio.positions["BTCUSDT"].avg_price == 25000.0
    assert portfolio.positions["BTCUSDT"].peak_price == 30000.0


def test_sync_can_reset_reserve():
    portfolio = Portfolio(100.0, reserve_usdt=40.0)
    sync_portfolio_from_exchange(
        portfolio,
        FakeTrading({"USDT": 90.0}),
        FakeMarket({}),
        [],
        keep_reserve=False,
    )
    assert portfolio.reserve_usdt == 0.0
    assert portfolio.cash_usdt == 90.0
