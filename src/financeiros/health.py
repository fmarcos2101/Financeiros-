from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from financeiros.models import utc_now


def heartbeat_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / "heartbeat.json"


def write_heartbeat(
    log_dir: str | Path,
    *,
    cycle_id: int | None = None,
    cycle_n: int | None = None,
    wealth_usdt: float | None = None,
    halted: bool | None = None,
    status: str = "ok",
    detail: str | None = None,
) -> Path:
    path = heartbeat_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now().isoformat(),
        "pid": os.getpid(),
        "status": status,
        "cycle_id": cycle_id,
        "cycle_n": cycle_n,
        "wealth_usdt": wealth_usdt,
        "halted": halted,
        "detail": detail,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_health(
    log_dir: str | Path,
    *,
    stale_after_seconds: int = 7200,
) -> dict:
    """Lê heartbeat e classifica se o loop parece vivo."""
    path = heartbeat_path(log_dir)
    now = utc_now()
    if not path.exists():
        return {
            "ok": False,
            "alive": False,
            "reason": "heartbeat_missing",
            "message": f"Arquivo de heartbeat não encontrado: {path}",
            "heartbeat": None,
            "checked_at": now.isoformat(),
            "stale_after_seconds": stale_after_seconds,
        }

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "alive": False,
            "reason": "heartbeat_invalid",
            "message": f"Heartbeat inválido: {exc}",
            "heartbeat": None,
            "checked_at": now.isoformat(),
            "stale_after_seconds": stale_after_seconds,
        }

    updated_raw = raw.get("updated_at")
    age_seconds = None
    alive = False
    reason = "ok"
    message = "Loop aparenta saudável."
    try:
        updated = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_seconds = (now - updated).total_seconds()
        if age_seconds > stale_after_seconds:
            alive = False
            reason = "heartbeat_stale"
            message = (
                f"Heartbeat antigo ({age_seconds:.0f}s > {stale_after_seconds}s). "
                "O run-loop pode ter parado."
            )
        else:
            alive = True
            if raw.get("status") == "error":
                alive = True  # processo vive, mas último ciclo falhou
                reason = "last_cycle_error"
                message = f"Loop vivo, mas último ciclo falhou: {raw.get('detail')}"
            elif raw.get("halted"):
                reason = "circuit_halted"
                message = "Loop vivo, porém circuit breaker está haltado."
    except Exception:
        reason = "heartbeat_invalid"
        message = "Não foi possível interpretar updated_at do heartbeat."

    return {
        "ok": alive and reason in {"ok", "circuit_halted"},
        "alive": alive,
        "reason": reason,
        "message": message,
        "age_seconds": None if age_seconds is None else round(age_seconds, 1),
        "heartbeat": raw,
        "checked_at": now.isoformat(),
        "stale_after_seconds": stale_after_seconds,
        "path": str(path),
    }
