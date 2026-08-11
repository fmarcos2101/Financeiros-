from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from financeiros.analysis.exits import ExitEngine
from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.capital.portfolio import Portfolio
from financeiros.capital.sizing import size_order
from financeiros.config import AppConfig
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.execution.paper import PaperBroker
from financeiros.models import Candle, Side


INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


@dataclass
class BacktestTrade:
    symbol: str
    side: str
    reason: str
    price: float
    quantity: float
    notional: float
    fee: float
    realized_pnl: float
    reserve_skim: float
    time: datetime


@dataclass
class BacktestReport:
    symbols: list[str]
    interval: str
    days: int
    bars: int
    starting_cash_usdt: float
    ending_cash_usdt: float
    ending_reserve_usdt: float
    ending_equity_usdt: float
    ending_wealth_usdt: float
    total_return_pct: float
    max_drawdown_pct: float
    trades: int
    buys: int
    sells: int
    stop_losses: int
    trailing_stops: int
    take_profits: int
    signal_sells: int
    win_rate: float | None
    profit_factor: float | None
    avg_trade_pnl: float | None
    gross_profit: float
    gross_loss: float
    reserve_skim_total: float
    trade_log: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)

    def to_dict(self, *, include_trades: bool = False, include_curve: bool = False) -> dict:
        payload = {
            "symbols": self.symbols,
            "interval": self.interval,
            "days": self.days,
            "bars": self.bars,
            "starting_cash_usdt": self.starting_cash_usdt,
            "ending_cash_usdt": round(self.ending_cash_usdt, 6),
            "ending_reserve_usdt": round(self.ending_reserve_usdt, 6),
            "ending_equity_usdt": round(self.ending_equity_usdt, 6),
            "ending_wealth_usdt": round(self.ending_wealth_usdt, 6),
            "total_return_pct": round(self.total_return_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "trades": self.trades,
            "buys": self.buys,
            "sells": self.sells,
            "stop_losses": self.stop_losses,
            "trailing_stops": self.trailing_stops,
            "take_profits": self.take_profits,
            "signal_sells": self.signal_sells,
            "win_rate": None if self.win_rate is None else round(self.win_rate, 4),
            "profit_factor": None if self.profit_factor is None else round(self.profit_factor, 4),
            "avg_trade_pnl": None if self.avg_trade_pnl is None else round(self.avg_trade_pnl, 6),
            "gross_profit": round(self.gross_profit, 6),
            "gross_loss": round(self.gross_loss, 6),
            "reserve_skim_total": round(self.reserve_skim_total, 6),
            "notes": (
                "Nenhum trade no período — filtros/sinais não geraram entradas."
                if self.trades == 0
                else None
            ),
        }
        if include_trades:
            payload["trade_log"] = [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "reason": t.reason,
                    "price": t.price,
                    "quantity": t.quantity,
                    "notional": t.notional,
                    "fee": t.fee,
                    "realized_pnl": t.realized_pnl,
                    "reserve_skim": t.reserve_skim,
                    "time": t.time.isoformat(),
                }
                for t in self.trade_log
            ]
        if include_curve:
            payload["equity_curve"] = self.equity_curve
        return payload


class BacktestEngine:
    """Replay offline da mesma lógica de sinal/risco/saídas/reserva."""

    def __init__(self, config: AppConfig, client: BinancePublicClient | None = None):
        self.config = config
        self.client = client or BinancePublicClient(
            base_url=config.exchange.base_url,
            timeout_seconds=config.exchange.timeout_seconds,
        )
        self.signals = SignalEngine(config.analysis)
        self.risk = RiskEngine(config.analysis, config.capital)
        self.exits = ExitEngine(config.exits)
        self.broker = PaperBroker(config.execution)

    def _skim_pct(self) -> float:
        if not self.config.capital.reserve_enabled:
            return 0.0
        return float(self.config.capital.reserve_skim_pct)

    def fetch_history(
        self,
        symbols: list[str],
        interval: str,
        days: int,
    ) -> dict[str, list[Candle]]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        # margem para warmup de indicadores
        warmup_bars = self.config.analysis.slow_sma + 5
        step = INTERVAL_MS.get(interval, 3_600_000)
        start_ms -= warmup_bars * step

        history: dict[str, list[Candle]] = {}
        for symbol in symbols:
            candles = self.client.fetch_klines_range(
                symbol=symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            if len(candles) < self.config.analysis.slow_sma + 2:
                raise RuntimeError(
                    f"Histórico insuficiente para {symbol}: {len(candles)} candles."
                )
            history[symbol] = candles
        return history

    @staticmethod
    def _align_timeline(history: dict[str, list[Candle]]) -> list[datetime]:
        sets = []
        for candles in history.values():
            sets.append({c.open_time for c in candles})
        common = set.intersection(*sets) if sets else set()
        return sorted(common)

    def run(
        self,
        *,
        days: int = 60,
        symbols: list[str] | None = None,
        interval: str | None = None,
        history: dict[str, list[Candle]] | None = None,
        curve_stride: int = 1,
    ) -> BacktestReport:
        symbols = symbols or list(self.config.universe.symbols)
        interval = interval or self.config.universe.interval
        if history is None:
            history = self.fetch_history(symbols, interval, days)

        # Index por tempo para lookup O(1)
        by_time: dict[str, dict[datetime, Candle]] = {
            sym: {c.open_time: c for c in candles} for sym, candles in history.items()
        }
        timeline = self._align_timeline(history)
        if not timeline:
            raise RuntimeError("Sem timestamps comuns entre os símbolos para alinhar o backtest.")

        portfolio = Portfolio(self.config.capital.starting_cash_usdt)
        skim_pct = self._skim_pct()
        warmup = self.config.analysis.slow_sma + 2

        trade_log: list[BacktestTrade] = []
        equity_curve: list[dict] = []
        peak_wealth = self.config.capital.starting_cash_usdt
        max_dd = 0.0

        # séries crescentes por símbolo
        series: dict[str, list[Candle]] = {s: [] for s in symbols}

        buys = sells = stops = trails = takes = signal_sells = 0
        realized_pnls: list[float] = []
        reserve_skim_total = 0.0

        for i, ts in enumerate(timeline):
            # alimenta janela
            for sym in symbols:
                candle = by_time[sym].get(ts)
                if candle is None:
                    continue
                series[sym].append(candle)

            if i < warmup:
                continue

            prices = {sym: series[sym][-1].close for sym in symbols if series[sym]}

            for sym in symbols:
                window = series[sym]
                if len(window) < warmup:
                    continue

                pos = portfolio.positions.get(sym)
                if pos and pos.quantity > 0:
                    advice = self.exits.evaluate(pos, window)
                    if advice is not None:
                        fill = self.broker.execute(
                            sym, Side.SELL, advice.quantity, advice.fill_price
                        )
                        outcome = portfolio.apply_fill(fill, reserve_skim_pct=skim_pct)
                        reserve_skim_total += outcome.reserve_skim
                        sells += 1
                        if advice.reason == "stop_loss":
                            stops += 1
                        elif advice.reason == "trailing_stop":
                            trails += 1
                        else:
                            takes += 1
                        realized_pnls.append(outcome.realized_pnl)
                        trade_log.append(
                            BacktestTrade(
                                symbol=sym,
                                side="sell",
                                reason=advice.reason,
                                price=fill.price,
                                quantity=fill.quantity,
                                notional=fill.notional,
                                fee=fill.fee,
                                realized_pnl=outcome.realized_pnl,
                                reserve_skim=outcome.reserve_skim,
                                time=ts,
                            )
                        )
                        prices[sym] = advice.fill_price
                        continue

                signal = self.signals.evaluate(window)
                prices[sym] = signal.price
                snapshot = portfolio.mark_to_market(prices)
                assessment = self.risk.assess(signal, snapshot)
                qty, _notional = size_order(signal, assessment, self.config.capital)
                if not (assessment.approved and qty > 0):
                    continue
                if signal.side not in (Side.BUY, Side.SELL):
                    continue

                fill = self.broker.execute(sym, signal.side, qty, signal.price)
                outcome = portfolio.apply_fill(fill, reserve_skim_pct=skim_pct)
                reserve_skim_total += outcome.reserve_skim
                reason = "signal_buy" if signal.side == Side.BUY else "signal_sell"
                if signal.side == Side.BUY:
                    buys += 1
                else:
                    sells += 1
                    signal_sells += 1
                    realized_pnls.append(outcome.realized_pnl)

                trade_log.append(
                    BacktestTrade(
                        symbol=sym,
                        side=signal.side.value,
                        reason=reason,
                        price=fill.price,
                        quantity=fill.quantity,
                        notional=fill.notional,
                        fee=fill.fee,
                        realized_pnl=outcome.realized_pnl,
                        reserve_skim=outcome.reserve_skim,
                        time=ts,
                    )
                )

            snap = portfolio.mark_to_market(prices)
            wealth = snap.total_wealth_usdt
            peak_wealth = max(peak_wealth, wealth)
            if peak_wealth > 0:
                dd = (peak_wealth - wealth) / peak_wealth
                max_dd = max(max_dd, dd)

            if (i % max(curve_stride, 1) == 0) or i == len(timeline) - 1:
                equity_curve.append(
                    {
                        "time": ts.isoformat(),
                        "equity_usdt": snap.equity_usdt,
                        "reserve_usdt": snap.reserve_usdt,
                        "wealth_usdt": wealth,
                    }
                )

        final_prices = {sym: series[sym][-1].close for sym in symbols if series[sym]}
        final = portfolio.mark_to_market(final_prices)
        starting = self.config.capital.starting_cash_usdt
        ret = (final.total_wealth_usdt - starting) / starting if starting else 0.0

        wins = [p for p in realized_pnls if p > 0]
        losses = [p for p in realized_pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        win_rate = (len(wins) / len(realized_pnls)) if realized_pnls else None
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
        avg_pnl = (sum(realized_pnls) / len(realized_pnls)) if realized_pnls else None

        return BacktestReport(
            symbols=symbols,
            interval=interval,
            days=days,
            bars=max(0, len(timeline) - warmup),
            starting_cash_usdt=starting,
            ending_cash_usdt=final.cash_usdt,
            ending_reserve_usdt=final.reserve_usdt,
            ending_equity_usdt=final.equity_usdt,
            ending_wealth_usdt=final.total_wealth_usdt,
            total_return_pct=ret * 100.0,
            max_drawdown_pct=max_dd * 100.0,
            trades=len(trade_log),
            buys=buys,
            sells=sells,
            stop_losses=stops,
            trailing_stops=trails,
            take_profits=takes,
            signal_sells=signal_sells,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_pnl=avg_pnl,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            reserve_skim_total=reserve_skim_total,
            trade_log=trade_log,
            equity_curve=equity_curve,
        )
