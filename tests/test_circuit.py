from datetime import datetime, timedelta, timezone

from financeiros.analysis.circuit import CircuitBreaker
from financeiros.config import CircuitBreakerConfig
from financeiros.memory.store import MemoryStore


def test_circuit_trips_on_daily_loss():
    cb = CircuitBreaker(
        CircuitBreakerConfig(
            enabled=True,
            max_daily_loss_pct=0.03,
            max_weekly_loss_pct=0.10,
            auto_resume_next_day=True,
        )
    )
    now = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    first = cb.evaluate(wealth_usdt=1000.0, now=now, state=None)
    assert first.halted is False
    assert first.day_start_wealth == 1000.0

    state = {
        "halted": 0,
        "halt_reason": None,
        "halted_at": None,
        "day_anchor_date": first.day_anchor_date,
        "day_start_wealth": first.day_start_wealth,
        "week_anchor_date": first.week_anchor_date,
        "week_start_wealth": first.week_start_wealth,
    }
    tripped = cb.evaluate(wealth_usdt=960.0, now=now, state=state)
    assert tripped.halted is True
    assert tripped.newly_tripped is True
    assert "daily_loss" in (tripped.reason or "")


def test_circuit_auto_resumes_next_day():
    cb = CircuitBreaker(
        CircuitBreakerConfig(
            enabled=True,
            max_daily_loss_pct=0.03,
            auto_resume_next_day=True,
        )
    )
    day1 = datetime(2024, 6, 1, 20, tzinfo=timezone.utc)
    state = {
        "halted": 1,
        "halt_reason": "daily_loss: -3.50% <= -3.00%",
        "halted_at": day1.isoformat(),
        "day_anchor_date": "2024-06-01",
        "day_start_wealth": 1000.0,
        "week_anchor_date": "2024-05-27",
        "week_start_wealth": 1000.0,
    }
    day2 = day1 + timedelta(days=1)
    resumed = cb.evaluate(wealth_usdt=970.0, now=day2, state=state)
    assert resumed.halted is False
    assert resumed.day_anchor_date == "2024-06-02"


def test_memory_resume_and_halt(tmp_path):
    store = MemoryStore(tmp_path / "c.db")
    store.halt_circuit("manual_halt")
    state = store.get_robot_state()
    assert state is not None
    assert state["halted"] == 1
    store.resume_circuit()
    state2 = store.get_robot_state()
    assert state2["halted"] == 0
    assert state2["halt_reason"] is None
