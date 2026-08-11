from pathlib import Path

from financeiros.capital.portfolio import Portfolio
from financeiros.config import ExecutionConfig
from financeiros.execution.paper import PaperBroker
from financeiros.memory.store import MemoryStore
from financeiros.models import Side


def test_reserve_skim_on_profitable_sell():
    portfolio = Portfolio(1000.0)
    broker = PaperBroker(ExecutionConfig(fee_bps=0))

    buy = broker.execute("BTCUSDT", Side.BUY, quantity=1.0, price=100.0)
    portfolio.apply_fill(buy)
    assert portfolio.cash_usdt == 900.0

    sell = broker.execute("BTCUSDT", Side.SELL, quantity=1.0, price=150.0)
    outcome = portfolio.apply_fill(sell, reserve_skim_pct=0.20)
    # Lucro 50; 20% = 10 vai para reserva
    assert abs(outcome.realized_pnl - 50.0) < 1e-9
    assert abs(outcome.reserve_skim - 10.0) < 1e-9
    assert abs(portfolio.reserve_usdt - 10.0) < 1e-9
    assert abs(portfolio.cash_usdt - 1040.0) < 1e-9  # 900+150-10


def test_no_reserve_skim_on_loss():
    portfolio = Portfolio(1000.0)
    broker = PaperBroker(ExecutionConfig(fee_bps=0))
    portfolio.apply_fill(broker.execute("ETHUSDT", Side.BUY, quantity=1.0, price=200.0))
    outcome = portfolio.apply_fill(
        broker.execute("ETHUSDT", Side.SELL, quantity=1.0, price=150.0),
        reserve_skim_pct=0.20,
    )
    assert outcome.realized_pnl < 0
    assert outcome.reserve_skim == 0.0
    assert portfolio.reserve_usdt == 0.0


def test_reserve_persists_and_survives_reset_keep(tmp_path: Path):
    db = tmp_path / "r.db"
    store = MemoryStore(db)
    p = store.load_portfolio(1000.0)
    broker = PaperBroker(ExecutionConfig(fee_bps=0))
    p.apply_fill(broker.execute("BTCUSDT", Side.BUY, quantity=1.0, price=100.0))
    outcome = p.apply_fill(
        broker.execute("BTCUSDT", Side.SELL, quantity=1.0, price=200.0),
        reserve_skim_pct=0.25,
    )
    assert outcome.reserve_skim == 25.0
    store.save_portfolio(p)
    store.record_reserve_transfer(
        amount=outcome.reserve_skim,
        realized_pnl=outcome.realized_pnl,
        skim_pct=0.25,
        symbol="BTCUSDT",
        note="test",
    )

    p2 = store.load_portfolio(1000.0)
    assert abs(p2.reserve_usdt - 25.0) < 1e-9

    store.reset_portfolio(1000.0, keep_reserve=True)
    p3 = store.load_portfolio(1000.0)
    assert p3.cash_usdt == 1000.0
    assert abs(p3.reserve_usdt - 25.0) < 1e-9
    assert store.get_status()["reserve_skimmed_total"] == 25.0
