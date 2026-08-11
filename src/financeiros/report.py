from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from financeiros.config import AppConfig
from financeiros.memory.store import MemoryStore
from financeiros.models import utc_now


@dataclass
class Alert:
    level: str  # info | warning | critical
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message}


@dataclass
class DailyReport:
    date: str
    generated_at: str
    summary: dict
    alerts: list[Alert] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    cycles: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "alerts": [a.to_dict() for a in self.alerts],
            "decisions": self.decisions,
            "fills": self.fills,
            "cycles": self.cycles,
        }

    def to_text(self) -> str:
        s = self.summary
        lines = [
            f"Relatório diário {self.date} (UTC)",
            f"Gerado em: {self.generated_at}",
            "",
            f"Wealth: {s.get('ending_wealth_usdt')} USDT "
            f"(dia {s.get('daily_pnl_pct')}% | semana {s.get('weekly_pnl_pct')}%)",
            f"Cash: {s.get('cash_usdt')} | Reserva: {s.get('reserve_usdt')}",
            f"Ciclos: {s.get('cycles')} | Decisões: {s.get('decisions')} | "
            f"Aprovadas: {s.get('approved')} | Fills: {s.get('fills')}",
            f"Buys: {s.get('buys')} | Sells: {s.get('sells')} | "
            f"Stops: {s.get('stop_losses')} | Trailing: {s.get('trailing_stops')} | "
            f"TP: {s.get('take_profits')}",
            f"Circuit: halted={s.get('circuit_halted')} reason={s.get('circuit_reason')}",
            "",
        ]
        if self.alerts:
            lines.append("Alertas:")
            for alert in self.alerts:
                lines.append(f"- [{alert.level}] {alert.code}: {alert.message}")
        else:
            lines.append("Alertas: nenhum")
        return "\n".join(lines) + "\n"


class DailyReporter:
    def __init__(self, config: AppConfig, memory: MemoryStore | None = None):
        self.config = config
        self.memory = memory or MemoryStore(config.memory.db_path)
        self.report_dir = Path(config.runtime.report_dir)

    def build(self, day: str | None = None) -> DailyReport:
        now = utc_now()
        day = day or now.date().isoformat()
        decisions = self.memory.decisions_between(f"{day}T00:00:00+00:00", f"{day}T23:59:59.999999+00:00")
        fills = self.memory.fills_between(f"{day}T00:00:00+00:00", f"{day}T23:59:59.999999+00:00")
        cycles = self.memory.cycles_between(f"{day}T00:00:00+00:00", f"{day}T23:59:59.999999+00:00")
        status = self.memory.get_status()
        robot = status.get("circuit_breaker") or {}

        buys = sells = stops = trails = tps = approved = 0
        for d in decisions:
            tags = d.get("tags") or "[]"
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            side = d.get("side")
            if d.get("approved"):
                approved += 1
            if side == "buy" and d.get("approved"):
                buys += 1
            if side == "sell" and d.get("approved"):
                sells += 1
            if "stop_loss" in tags:
                stops += 1
            if "trailing_stop" in tags:
                trails += 1
            if "take_profit" in tags:
                tps += 1

        day_start = robot.get("day_start_wealth")
        week_start = robot.get("week_start_wealth")
        # ending wealth: prefer last cycle of the day, else current status cash+reserve
        ending_wealth = None
        if cycles:
            last = cycles[-1]
            summary_json = last.get("summary_json") or "{}"
            try:
                summary = json.loads(summary_json) if isinstance(summary_json, str) else summary_json
            except Exception:
                summary = {}
            ending_wealth = summary.get("total_wealth_usdt")
            if ending_wealth is None:
                ending_wealth = float(last.get("equity_usdt") or 0.0) + float(
                    status.get("reserve_usdt") or 0.0
                )
        if ending_wealth is None:
            cash = float(status.get("cash_usdt") or 0.0)
            reserve = float(status.get("reserve_usdt") or 0.0)
            ending_wealth = cash + reserve

        daily_pnl_pct = None
        weekly_pnl_pct = None
        if day_start and float(day_start) > 0:
            daily_pnl_pct = round(((ending_wealth - float(day_start)) / float(day_start)) * 100.0, 4)
        if week_start and float(week_start) > 0:
            weekly_pnl_pct = round(((ending_wealth - float(week_start)) / float(week_start)) * 100.0, 4)

        summary = {
            "cash_usdt": status.get("cash_usdt"),
            "reserve_usdt": status.get("reserve_usdt"),
            "ending_wealth_usdt": round(float(ending_wealth), 6),
            "daily_pnl_pct": daily_pnl_pct,
            "weekly_pnl_pct": weekly_pnl_pct,
            "cycles": len(cycles),
            "decisions": len(decisions),
            "approved": approved,
            "fills": len(fills),
            "buys": buys,
            "sells": sells,
            "stop_losses": stops,
            "trailing_stops": trails,
            "take_profits": tps,
            "open_positions": len(status.get("positions") or []),
            "circuit_halted": bool(robot.get("halted")),
            "circuit_reason": robot.get("reason"),
        }

        alerts = self._build_alerts(summary)
        return DailyReport(
            date=day,
            generated_at=now.isoformat(),
            summary=summary,
            alerts=alerts,
            decisions=decisions,
            fills=fills,
            cycles=cycles,
        )

    def _build_alerts(self, summary: dict) -> list[Alert]:
        alerts: list[Alert] = []
        cb = self.config.circuit_breaker
        daily = summary.get("daily_pnl_pct")
        weekly = summary.get("weekly_pnl_pct")

        if summary.get("circuit_halted"):
            alerts.append(
                Alert(
                    level="critical",
                    code="circuit_halted",
                    message=f"Circuit breaker ativo: {summary.get('circuit_reason')}",
                )
            )

        warn_daily = -abs(self.config.runtime.alert_daily_loss_pct) * 100.0
        warn_weekly = -abs(self.config.runtime.alert_weekly_loss_pct) * 100.0
        crit_daily = -abs(cb.max_daily_loss_pct) * 100.0
        crit_weekly = -abs(cb.max_weekly_loss_pct) * 100.0

        if daily is not None:
            if daily <= crit_daily:
                alerts.append(
                    Alert(
                        level="critical",
                        code="daily_loss_limit",
                        message=f"Perda diária {daily}% atingiu/ultrapassou o limite ({crit_daily}%).",
                    )
                )
            elif daily <= warn_daily:
                alerts.append(
                    Alert(
                        level="warning",
                        code="daily_loss_warning",
                        message=f"Perda diária {daily}% próxima do limite ({crit_daily}%).",
                    )
                )

        if weekly is not None:
            if weekly <= crit_weekly:
                alerts.append(
                    Alert(
                        level="critical",
                        code="weekly_loss_limit",
                        message=f"Perda semanal {weekly}% atingiu/ultrapassou o limite ({crit_weekly}%).",
                    )
                )
            elif weekly <= warn_weekly:
                alerts.append(
                    Alert(
                        level="warning",
                        code="weekly_loss_warning",
                        message=f"Perda semanal {weekly}% próxima do limite ({crit_weekly}%).",
                    )
                )

        if summary.get("cycles", 0) == 0:
            alerts.append(
                Alert(
                    level="warning",
                    code="no_cycles",
                    message="Nenhum ciclo registrado neste dia.",
                )
            )
        elif summary.get("fills", 0) == 0 and summary.get("decisions", 0) > 0:
            alerts.append(
                Alert(
                    level="info",
                    code="no_fills",
                    message="Houve decisões, mas nenhum fill no dia.",
                )
            )

        if summary.get("stop_losses", 0) >= self.config.runtime.alert_stop_count:
            alerts.append(
                Alert(
                    level="warning",
                    code="many_stops",
                    message=(
                        f"{summary.get('stop_losses')} stop-loss no dia "
                        f"(limite de alerta: {self.config.runtime.alert_stop_count})."
                    ),
                )
            )

        return alerts

    def save(self, report: DailyReport) -> Path:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.report_dir / f"{report.date}.json"
        txt_path = self.report_dir / f"{report.date}.txt"
        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        txt_path.write_text(report.to_text(), encoding="utf-8")
        self.memory.set_last_report_date(report.date)
        return json_path

    def maybe_emit_for_new_day(self, current_day: str | None = None) -> DailyReport | None:
        """Se virou o dia UTC, gera relatório do dia anterior (uma vez)."""
        now = utc_now()
        today = current_day or now.date().isoformat()
        last = self.memory.get_last_report_date()
        # Gera o relatório de ontem quando detecta virada e ainda não emitiu
        from datetime import timedelta

        yesterday = (now.date() - timedelta(days=1)).isoformat()
        if last == yesterday or last == today:
            return None
        # Só emite ontem se já existe algum dado; senão marca e segue
        report = self.build(yesterday)
        if report.summary.get("cycles", 0) == 0 and report.summary.get("decisions", 0) == 0:
            self.memory.set_last_report_date(yesterday)
            return None
        self.save(report)
        return report
