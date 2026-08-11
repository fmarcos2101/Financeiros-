import json
from pathlib import Path

from financeiros.config import AppConfig
from financeiros.dashboard import STATIC_DIR, build_snapshot, serve_dashboard
from financeiros.health import write_heartbeat
from financeiros.memory.store import MemoryStore


def test_static_dashboard_exists():
    assert (STATIC_DIR / "dashboard.html").exists()


def test_build_snapshot_from_empty_store(tmp_path: Path):
    config = AppConfig()
    config.memory.db_path = str(tmp_path / "mem.db")
    config.runtime.log_dir = str(tmp_path / "logs")
    config.capital.starting_cash_usdt = 1000.0

    memory = MemoryStore(config.memory.db_path)
    memory.reset_portfolio(1000.0)
    write_heartbeat(config.runtime.log_dir, cycle_id=1, wealth_usdt=1000.0, status="ok")

    snap = build_snapshot(config)
    assert snap["mode"] == "paper"
    assert snap["live_ready"] is False
    assert snap["portfolio"]["cash_usdt"] == 1000.0
    assert snap["portfolio"]["total_wealth_usdt"] == 1000.0
    assert snap["health"]["alive"] is True
    assert isinstance(snap["cycles"], list)
    assert isinstance(snap["decisions"], list)


def test_dashboard_http_snapshot(tmp_path: Path):
    import urllib.request

    config = AppConfig()
    config.memory.db_path = str(tmp_path / "mem.db")
    config.runtime.log_dir = str(tmp_path / "logs")
    MemoryStore(config.memory.db_path).reset_portfolio(1000.0)
    write_heartbeat(config.runtime.log_dir, status="ok", wealth_usdt=1000.0)

    server = serve_dashboard(config, host="127.0.0.1", port=0)
    host, port = server.server_address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/snapshot", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload["portfolio"]["cash_usdt"] == 1000.0

        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        assert "Financeiros" in html
        assert "/api/snapshot" in html
    finally:
        server.shutdown()
        server.server_close()
