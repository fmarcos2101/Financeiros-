from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from financeiros.config import CircuitBreakerConfig


def _utc_day(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).date().isoformat()


def _utc_week_start(ts: datetime) -> str:
    d = ts.astimezone(timezone.utc).date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


@dataclass
class CircuitSnapshot:
    halted: bool
    reason: str | None
    halted_at: str | None
    day_anchor_date: str
    day_start_wealth: float
    week_anchor_date: str
    week_start_wealth: float
    daily_pnl_pct: float
    weekly_pnl_pct: float
    newly_tripped: bool = False

    def to_dict(self) -> dict:
        return {
            "halted": self.halted,
            "reason": self.reason,
            "halted_at": self.halted_at,
            "day_anchor_date": self.day_anchor_date,
            "day_start_wealth": round(self.day_start_wealth, 6),
            "week_anchor_date": self.week_anchor_date,
            "week_start_wealth": round(self.week_start_wealth, 6),
            "daily_pnl_pct": round(self.daily_pnl_pct, 4),
            "weekly_pnl_pct": round(self.weekly_pnl_pct, 4),
            "newly_tripped": self.newly_tripped,
        }


class CircuitBreaker:
    """Pausa novas entradas se a perda diária/semanal exceder o limite."""

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config

    def evaluate(
        self,
        *,
        wealth_usdt: float,
        now: datetime,
        state: dict | None,
    ) -> CircuitSnapshot:
        """Atualiza âncoras dia/semana e decide se o robô deve permanecer haltado."""
        day = _utc_day(now)
        week = _utc_week_start(now)
        state = state or {}

        day_anchor = state.get("day_anchor_date") or day
        day_start = float(state.get("day_start_wealth") or wealth_usdt)
        week_anchor = state.get("week_anchor_date") or week
        week_start = float(state.get("week_start_wealth") or wealth_usdt)

        if day_anchor != day:
            day_anchor = day
            day_start = wealth_usdt
        if week_anchor != week:
            week_anchor = week
            week_start = wealth_usdt

        daily_pnl = ((wealth_usdt - day_start) / day_start) if day_start > 0 else 0.0
        weekly_pnl = ((wealth_usdt - week_start) / week_start) if week_start > 0 else 0.0

        halted = bool(state.get("halted"))
        reason = state.get("halt_reason")
        halted_at = state.get("halted_at")
        newly_tripped = False

        if not self.config.enabled:
            return CircuitSnapshot(
                halted=False,
                reason=None,
                halted_at=None,
                day_anchor_date=day_anchor,
                day_start_wealth=day_start,
                week_anchor_date=week_anchor,
                week_start_wealth=week_start,
                daily_pnl_pct=daily_pnl * 100.0,
                weekly_pnl_pct=weekly_pnl * 100.0,
                newly_tripped=False,
            )

        # Auto-resume opcional na virada do dia (só se o trip foi diário)
        if (
            halted
            and self.config.auto_resume_next_day
            and reason
            and reason.startswith("daily_loss")
            and state.get("day_anchor_date") != day
        ):
            halted = False
            reason = None
            halted_at = None

        if not halted:
            if daily_pnl <= -abs(self.config.max_daily_loss_pct):
                halted = True
                newly_tripped = True
                reason = (
                    f"daily_loss: {daily_pnl:.2%} <= "
                    f"-{self.config.max_daily_loss_pct:.2%}"
                )
                halted_at = now.astimezone(timezone.utc).isoformat()
            elif weekly_pnl <= -abs(self.config.max_weekly_loss_pct):
                halted = True
                newly_tripped = True
                reason = (
                    f"weekly_loss: {weekly_pnl:.2%} <= "
                    f"-{self.config.max_weekly_loss_pct:.2%}"
                )
                halted_at = now.astimezone(timezone.utc).isoformat()

        return CircuitSnapshot(
            halted=halted,
            reason=reason,
            halted_at=halted_at,
            day_anchor_date=day_anchor,
            day_start_wealth=day_start,
            week_anchor_date=week_anchor,
            week_start_wealth=week_start,
            daily_pnl_pct=daily_pnl * 100.0,
            weekly_pnl_pct=weekly_pnl * 100.0,
            newly_tripped=newly_tripped,
        )
