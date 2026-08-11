from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from financeiros.backtest import BacktestEngine
from financeiros.config import AppConfig
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.models import Candle
from financeiros.tune import StrategyTuner


def _filter_history_until(
    history: dict[str, list[Candle]],
    end_exclusive: datetime,
) -> dict[str, list[Candle]]:
    return {
        symbol: [c for c in candles if c.open_time < end_exclusive]
        for symbol, candles in history.items()
    }


def _summary(report) -> dict:
    return {
        "total_return_pct": round(report.total_return_pct, 4),
        "max_drawdown_pct": round(report.max_drawdown_pct, 4),
        "trades": report.trades,
        "win_rate": None if report.win_rate is None else round(report.win_rate, 4),
        "profit_factor": None if report.profit_factor is None else round(report.profit_factor, 4),
        "ending_wealth_usdt": round(report.ending_wealth_usdt, 6),
        "stop_losses": report.stop_losses,
        "trailing_stops": report.trailing_stops,
        "take_profits": report.take_profits,
        "bars": report.bars,
    }


def _gates(
    holdout: dict,
    train: dict,
    *,
    min_holdout_return_pct: float,
    max_holdout_dd_pct: float,
    min_holdout_trades: int,
    max_return_drop_pct: float,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if holdout["trades"] < min_holdout_trades:
        reasons.append(
            f"poucos trades no holdout ({holdout['trades']} < {min_holdout_trades})"
        )
    if holdout["total_return_pct"] < min_holdout_return_pct:
        reasons.append(
            f"retorno holdout {holdout['total_return_pct']}% < mínimo {min_holdout_return_pct}%"
        )
    if holdout["max_drawdown_pct"] > max_holdout_dd_pct:
        reasons.append(
            f"drawdown holdout {holdout['max_drawdown_pct']}% > máximo {max_holdout_dd_pct}%"
        )
    drop = train["total_return_pct"] - holdout["total_return_pct"]
    if drop > max_return_drop_pct:
        reasons.append(
            f"degradação train→holdout {drop:.2f}pp > {max_return_drop_pct}pp"
        )
    return len(reasons) == 0, reasons


class OutOfSampleValidator:
    """Tune/avalia no período de treino e valida em janela holdout nunca vista."""

    def __init__(self, config: AppConfig, client: BinancePublicClient | None = None):
        self.config = config
        self.client = client

    def run(
        self,
        *,
        train_days: int = 60,
        holdout_days: int = 30,
        symbols: list[str] | None = None,
        interval: str | None = None,
        tune: bool = True,
        max_dd_limit: float = 8.0,
        min_holdout_return_pct: float = -2.0,
        max_holdout_dd_pct: float = 6.0,
        min_holdout_trades: int = 2,
        max_return_drop_pct: float = 5.0,
        history: dict[str, list[Candle]] | None = None,
    ) -> dict:
        symbols = symbols or list(self.config.universe.symbols)
        interval = interval or self.config.universe.interval
        total_days = train_days + holdout_days

        probe = BacktestEngine(self.config, client=self.client)
        if history is None:
            history = probe.fetch_history(symbols, interval, total_days)

        split_at = datetime.now(timezone.utc) - timedelta(days=holdout_days)
        train_history = _filter_history_until(history, split_at)
        for symbol, candles in train_history.items():
            if len(candles) < self.config.analysis.slow_sma + 5:
                raise RuntimeError(
                    f"Histórico de treino insuficiente para {symbol}: {len(candles)} candles."
                )

        tuned = None
        cfg = deepcopy(self.config)
        if tune:
            tuner = StrategyTuner(cfg, client=self.client)
            tuned = tuner.run(
                days=train_days,
                symbols=symbols,
                interval=interval,
                max_dd_limit=max_dd_limit,
                top_n=3,
                history=train_history,
            )
            best = tuned.get("best")
            if best:
                cfg.exits.stop_loss_pct = best["stop_loss_pct"]
                cfg.exits.take_profit_pct = best["take_profit_pct"]
                cfg.exits.trailing_enabled = best["trailing_enabled"]
                cfg.exits.trailing_pct = best["trailing_pct"]
                cfg.exits.trailing_activation_pct = best["trailing_activation_pct"]

        train_engine = BacktestEngine(cfg, client=self.client)
        train_report = train_engine.run(
            days=train_days,
            symbols=symbols,
            interval=interval,
            history=train_history,
            curve_stride=10_000,
        )

        holdout_engine = BacktestEngine(cfg, client=self.client)
        holdout_report = holdout_engine.run(
            days=holdout_days,
            symbols=symbols,
            interval=interval,
            history=history,
            curve_stride=10_000,
            active_after=split_at,
        )

        train_s = _summary(train_report)
        holdout_s = _summary(holdout_report)
        passed, fail_reasons = _gates(
            holdout_s,
            train_s,
            min_holdout_return_pct=min_holdout_return_pct,
            max_holdout_dd_pct=max_holdout_dd_pct,
            min_holdout_trades=min_holdout_trades,
            max_return_drop_pct=max_return_drop_pct,
        )

        return {
            "train_days": train_days,
            "holdout_days": holdout_days,
            "split_at": split_at.isoformat(),
            "symbols": symbols,
            "interval": interval,
            "tuned": bool(tune),
            "params_used": {
                "stop_loss_pct": cfg.exits.stop_loss_pct,
                "take_profit_pct": cfg.exits.take_profit_pct,
                "trailing_enabled": cfg.exits.trailing_enabled,
                "trailing_pct": cfg.exits.trailing_pct,
                "trailing_activation_pct": cfg.exits.trailing_activation_pct,
            },
            "tune": {
                "tested": tuned.get("tested") if tuned else 0,
                "best": tuned.get("best") if tuned else None,
            },
            "train": train_s,
            "holdout": holdout_s,
            "delta": {
                "return_pp": round(
                    holdout_s["total_return_pct"] - train_s["total_return_pct"], 4
                ),
                "max_drawdown_pp": round(
                    holdout_s["max_drawdown_pct"] - train_s["max_drawdown_pct"], 4
                ),
                "trades": holdout_s["trades"] - train_s["trades"],
            },
            "passed": passed,
            "fail_reasons": fail_reasons,
            "verdict": "PASS" if passed else "FAIL",
        }
