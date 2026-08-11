from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from financeiros.config import AppConfig
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.health import read_health
from financeiros.memory.store import MemoryStore
from financeiros.models import utc_now


STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_snapshot(config: AppConfig) -> dict:
    """Monta o payload do dashboard a partir do SQLite + heartbeat + preços."""
    memory = MemoryStore(config.memory.db_path)
    status = memory.get_status()
    health = read_health(
        config.runtime.log_dir,
        stale_after_seconds=config.runtime.heartbeat_stale_seconds,
    )

    cash = float(status.get("cash_usdt") or 0.0)
    reserve = float(status.get("reserve_usdt") or 0.0)
    positions = list(status.get("positions") or [])
    equity = cash
    mark_error = None

    if positions:
        try:
            client = BinancePublicClient(
                base_url=config.exchange.base_url,
                timeout_seconds=config.exchange.timeout_seconds,
            )
            enriched = []
            for pos in positions:
                px = client.fetch_price(pos["symbol"])
                qty = float(pos["quantity"])
                avg = float(pos["avg_price"])
                mtm = qty * px
                equity += mtm
                pnl_pct = ((px - avg) / avg * 100.0) if avg else 0.0
                enriched.append(
                    {
                        **pos,
                        "mark_price": round(px, 8),
                        "market_value": round(mtm, 6),
                        "unrealized_pnl_pct": round(pnl_pct, 4),
                    }
                )
            positions = enriched
        except Exception as exc:
            mark_error = str(exc)

    wealth = equity + reserve
    starting = float(config.capital.starting_cash_usdt)
    total_return_pct = ((wealth - starting) / starting * 100.0) if starting else 0.0

    cycles = memory.recent_cycles(limit=12)
    decisions = memory.recent_decisions(limit=20)

    return {
        "checked_at": utc_now().isoformat(),
        "mode": config.mode,
        "live_ready": False,
        "live_note": (
            "Paper only: ordens reais na Binance ainda não estão implementadas. "
            "API keys no .env ficam reservadas para uma fase live futura."
        ),
        "universe": {
            "symbols": list(config.universe.symbols),
            "interval": config.universe.interval,
        },
        "health": health,
        "portfolio": {
            "cash_usdt": round(cash, 6),
            "reserve_usdt": round(reserve, 6),
            "equity_usdt": round(equity, 6),
            "total_wealth_usdt": round(wealth, 6),
            "starting_cash_usdt": starting,
            "total_return_pct": round(total_return_pct, 4),
            "reserve_skimmed_total": status.get("reserve_skimmed_total"),
            "positions": positions,
            "mark_to_market_error": mark_error,
        },
        "circuit_breaker": status.get("circuit_breaker"),
        "counts": {
            "decisions": status.get("decision_count"),
            "fills": status.get("fill_count"),
            "open_positions": len(positions),
        },
        "last_cycle": status.get("last_cycle"),
        "cycles": cycles,
        "decisions": decisions,
        "exits": {
            "enabled": config.exits.enabled,
            "stop_loss_pct": config.exits.stop_loss_pct,
            "take_profit_pct": config.exits.take_profit_pct,
            "trailing_enabled": config.exits.trailing_enabled,
            "trailing_pct": config.exits.trailing_pct,
            "trailing_activation_pct": config.exits.trailing_activation_pct,
        },
    }


class DashboardHandler(BaseHTTPRequestHandler):
    config: AppConfig

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Mantém o terminal limpo; erros ainda vão para stderr via send_error.
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            html_path = STATIC_DIR / "dashboard.html"
            if not html_path.exists():
                self._send(500, b"dashboard.html missing", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                html_path.read_bytes(),
                "text/html; charset=utf-8",
            )
            return

        if path == "/api/snapshot":
            try:
                payload = build_snapshot(self.config)
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            except Exception as exc:
                err = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(500, err, "application/json; charset=utf-8")
            return

        if path == "/api/health":
            health = read_health(
                self.config.runtime.log_dir,
                stale_after_seconds=self.config.runtime.heartbeat_stale_seconds,
            )
            body = json.dumps(health, ensure_ascii=False).encode("utf-8")
            code = 200 if health.get("alive") else 503
            self._send(code, body, "application/json; charset=utf-8")
            return

        self._send(404, b'{"error":"not found"}', "application/json; charset=utf-8")


def serve_dashboard(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> ThreadingHTTPServer:
    """Sobe o servidor em thread daemon (útil para testes)."""
    handler_cls = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"config": config},
    )
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run_dashboard(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    handler_cls = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"config": config},
    )
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"Dashboard: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard encerrado.", flush=True)
    finally:
        server.server_close()
