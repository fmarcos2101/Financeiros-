import json
from datetime import timedelta
from pathlib import Path

from financeiros.health import heartbeat_path, read_health, write_heartbeat
from financeiros.models import utc_now


def test_write_and_read_healthy_heartbeat(tmp_path: Path):
    path = write_heartbeat(
        tmp_path,
        cycle_id=7,
        cycle_n=3,
        wealth_usdt=1000.5,
        halted=False,
        status="ok",
    )
    assert path == heartbeat_path(tmp_path)
    assert path.exists()

    health = read_health(tmp_path, stale_after_seconds=7200)
    assert health["alive"] is True
    assert health["ok"] is True
    assert health["reason"] == "ok"
    assert health["heartbeat"]["cycle_id"] == 7
    assert health["age_seconds"] is not None
    assert health["age_seconds"] < 5


def test_missing_heartbeat(tmp_path: Path):
    health = read_health(tmp_path, stale_after_seconds=60)
    assert health["alive"] is False
    assert health["ok"] is False
    assert health["reason"] == "heartbeat_missing"


def test_stale_heartbeat(tmp_path: Path):
    path = write_heartbeat(tmp_path, cycle_id=1, status="ok")
    payload = json.loads(path.read_text(encoding="utf-8"))
    old = utc_now() - timedelta(hours=3)
    payload["updated_at"] = old.isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")

    health = read_health(tmp_path, stale_after_seconds=7200)
    assert health["alive"] is False
    assert health["reason"] == "heartbeat_stale"


def test_last_cycle_error_still_alive(tmp_path: Path):
    write_heartbeat(tmp_path, status="error", detail="boom", cycle_n=2)
    health = read_health(tmp_path, stale_after_seconds=7200)
    assert health["alive"] is True
    assert health["ok"] is False
    assert health["reason"] == "last_cycle_error"


def test_circuit_halted_reason(tmp_path: Path):
    write_heartbeat(tmp_path, status="ok", halted=True, wealth_usdt=900)
    health = read_health(tmp_path, stale_after_seconds=7200)
    assert health["alive"] is True
    assert health["ok"] is True
    assert health["reason"] == "circuit_halted"
