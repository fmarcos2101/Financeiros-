from pathlib import Path

from financeiros.capital.portfolio import Portfolio
from financeiros.execution.paper import PaperBroker
from financeiros.config import ExecutionConfig
from financeiros.memory.store import MemoryStore
from financeiros.models import Side


def test_portfolio_persists_across_loads(tmp_path: Path):
    db = tmp_path / "state.db"
    store = MemoryStore(db)

    p1 = store.load_portfolio(1000.0)
    assert p1.cash_usdt == 1000.0

    broker = PaperBroker(ExecutionConfig(fee_bps=0))
    fill = broker.execute("BTCUSDT", Side.BUY, quantity=0.01, price=50_000.0)
    p1.apply_fill(fill)
    store.save_portfolio(p1)

    p2 = store.load_portfolio(1000.0)
    assert abs(p2.cash_usdt - (1000.0 - 500.0)) < 1e-6
    assert "BTCUSDT" in p2.positions
    assert abs(p2.positions["BTCUSDT"].quantity - 0.01) < 1e-12


def test_reset_portfolio_keeps_history(tmp_path: Path):
    from financeiros.models import Decision

    db = tmp_path / "state.db"
    store = MemoryStore(db)
    p = store.load_portfolio(1000.0)
    broker = PaperBroker(ExecutionConfig(fee_bps=0))
    p.apply_fill(broker.execute("ETHUSDT", Side.BUY, quantity=1.0, price=100.0))
    store.save_portfolio(p)
    store.record_decision(
        Decision(
            symbol="ETHUSDT",
            side=Side.BUY,
            approved=True,
            rationale="test",
            signal_strength=0.5,
            price=100.0,
            quantity=1.0,
            notional=100.0,
        )
    )

    store.reset_portfolio(1000.0)
    restored = store.load_portfolio(999.0)  # starting ignorado se já existe cash row
    assert restored.cash_usdt == 1000.0
    assert restored.positions == {}
    assert len(store.recent_decisions(limit=5)) == 1


def test_record_cycle_and_status(tmp_path: Path):
    from financeiros.models import Decision

    db = tmp_path / "state.db"
    store = MemoryStore(db)
    store.load_portfolio(1000.0)
    decision = Decision(
        symbol="BTCUSDT",
        side=Side.HOLD,
        approved=False,
        rationale="hold",
        signal_strength=0.0,
        price=1.0,
    )
    cycle_id = store.record_cycle(
        started_at="2024-01-01T00:00:00+00:00",
        finished_at="2024-01-01T00:00:01+00:00",
        cash_usdt=1000.0,
        equity_usdt=1000.0,
        decisions=[decision],
    )
    assert cycle_id == 1
    status = store.get_status()
    assert status["cash_usdt"] == 1000.0
    assert status["last_cycle"]["id"] == 1
    assert len(store.recent_cycles(limit=5)) == 1
