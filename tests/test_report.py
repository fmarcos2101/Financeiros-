from pathlib import Path

from financeiros.config import AppConfig
from financeiros.memory.store import MemoryStore
from financeiros.models import Decision, Side, utc_now
from financeiros.report import DailyReporter


def test_daily_report_builds_and_alerts(tmp_path: Path):
    db = tmp_path / "r.db"
    config = AppConfig()
    config.memory.db_path = str(db)
    config.runtime.report_dir = str(tmp_path / "reports")
    config.runtime.alert_stop_count = 1
    config.circuit_breaker.max_daily_loss_pct = 0.03

    memory = MemoryStore(db)
    memory.load_portfolio(1000.0)
    memory.save_circuit_state(
        halted=True,
        reason="daily_loss: -3.20% <= -3.00%",
        halted_at=utc_now().isoformat(),
        day_anchor_date=utc_now().date().isoformat(),
        day_start_wealth=1000.0,
        week_anchor_date=utc_now().date().isoformat(),
        week_start_wealth=1000.0,
    )
    day = utc_now().date().isoformat()
    memory.record_decision(
        Decision(
            symbol="BTCUSDT",
            side=Side.SELL,
            approved=True,
            rationale="Stop-loss atingido",
            signal_strength=1.0,
            price=100.0,
            quantity=0.1,
            notional=10.0,
            tags=["paper", "exit", "stop_loss", "sell"],
        )
    )
    memory.record_cycle(
        started_at=f"{day}T10:00:00+00:00",
        finished_at=f"{day}T10:01:00+00:00",
        cash_usdt=960.0,
        equity_usdt=960.0,
        decisions=[],
        reserve_usdt=0.0,
        total_wealth_usdt=960.0,
    )

    reporter = DailyReporter(config, memory=memory)
    report = reporter.build(day)
    assert report.summary["cycles"] == 1
    assert report.summary["stop_losses"] >= 1
    assert report.summary["circuit_halted"] is True
    codes = {a.code for a in report.alerts}
    assert "circuit_halted" in codes
    assert "many_stops" in codes

    path = reporter.save(report)
    assert path.exists()
    assert (Path(config.runtime.report_dir) / f"{day}.txt").exists()
    assert memory.get_last_report_date() == day


def test_maybe_emit_skips_when_already_reported(tmp_path: Path):
    db = tmp_path / "r2.db"
    config = AppConfig()
    config.memory.db_path = str(db)
    config.runtime.report_dir = str(tmp_path / "reports2")
    memory = MemoryStore(db)
    memory.set_last_report_date(utc_now().date().isoformat())
    reporter = DailyReporter(config, memory=memory)
    assert reporter.maybe_emit_for_new_day() is None
