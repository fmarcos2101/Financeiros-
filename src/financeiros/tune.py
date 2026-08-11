from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import product

from financeiros.backtest import BacktestEngine, BacktestReport
from financeiros.config import AppConfig
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.models import Candle


@dataclass
class TuneCandidate:
    stop_loss_pct: float
    take_profit_pct: float
    trailing_enabled: bool
    trailing_pct: float
    trailing_activation_pct: float
    total_return_pct: float
    max_drawdown_pct: float
    trades: int
    win_rate: float | None
    profit_factor: float | None
    stop_losses: int
    trailing_stops: int
    take_profits: int
    ending_wealth_usdt: float
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


def _score(report: BacktestReport, *, max_dd_limit: float) -> float:
    """Score simples: retorno penalizado por drawdown e poucos trades."""
    if report.trades < 3:
        return -1e9
    if report.max_drawdown_pct > max_dd_limit:
        return report.total_return_pct - (report.max_drawdown_pct - max_dd_limit) * 2.0
    pf = report.profit_factor if report.profit_factor is not None else 0.0
    wr = report.win_rate if report.win_rate is not None else 0.0
    return report.total_return_pct + pf * 0.5 + wr * 2.0


class StrategyTuner:
    """Grid search leve sobre parâmetros de saída, reusando o mesmo histórico."""

    def __init__(self, config: AppConfig, client: BinancePublicClient | None = None):
        self.base_config = config
        self.client = client

    def run(
        self,
        *,
        days: int = 60,
        symbols: list[str] | None = None,
        interval: str | None = None,
        max_dd_limit: float = 8.0,
        top_n: int = 5,
        history: dict[str, list[Candle]] | None = None,
    ) -> dict:
        symbols = symbols or list(self.base_config.universe.symbols)
        interval = interval or self.base_config.universe.interval

        probe = BacktestEngine(self.base_config, client=self.client)
        if history is None:
            history = probe.fetch_history(symbols, interval, days)

        grid_stops = [0.02, 0.03, 0.04]
        grid_tps = [0.06, 0.10, 0.15]
        grid_trailing = [
            (False, 0.0, 0.0),
            (True, 0.02, 0.015),
            (True, 0.025, 0.02),
            (True, 0.03, 0.025),
        ]

        candidates: list[TuneCandidate] = []
        for stop, tp, (trail_on, trail_pct, trail_act) in product(
            grid_stops, grid_tps, grid_trailing
        ):
            cfg = deepcopy(self.base_config)
            cfg.exits.stop_loss_pct = stop
            cfg.exits.take_profit_pct = tp
            cfg.exits.trailing_enabled = trail_on
            cfg.exits.trailing_pct = trail_pct
            cfg.exits.trailing_activation_pct = trail_act

            engine = BacktestEngine(cfg, client=self.client)
            report = engine.run(
                days=days,
                symbols=symbols,
                interval=interval,
                history=history,
                curve_stride=10_000,
            )
            candidates.append(
                TuneCandidate(
                    stop_loss_pct=stop,
                    take_profit_pct=tp,
                    trailing_enabled=trail_on,
                    trailing_pct=trail_pct,
                    trailing_activation_pct=trail_act,
                    total_return_pct=round(report.total_return_pct, 4),
                    max_drawdown_pct=round(report.max_drawdown_pct, 4),
                    trades=report.trades,
                    win_rate=None if report.win_rate is None else round(report.win_rate, 4),
                    profit_factor=None
                    if report.profit_factor is None
                    else round(report.profit_factor, 4),
                    stop_losses=report.stop_losses,
                    trailing_stops=report.trailing_stops,
                    take_profits=report.take_profits,
                    ending_wealth_usdt=round(report.ending_wealth_usdt, 6),
                    score=round(_score(report, max_dd_limit=max_dd_limit), 4),
                )
            )

        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        best = ranked[0] if ranked else None
        return {
            "days": days,
            "symbols": symbols,
            "interval": interval,
            "max_dd_limit": max_dd_limit,
            "tested": len(candidates),
            "best": best.to_dict() if best else None,
            "top": [c.to_dict() for c in ranked[:top_n]],
        }
