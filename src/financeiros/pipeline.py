from __future__ import annotations

from dataclasses import dataclass

from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.capital.portfolio import Portfolio
from financeiros.capital.sizing import size_order
from financeiros.config import AppConfig
from financeiros.data.market import MarketDataService
from financeiros.execution.paper import PaperBroker
from financeiros.memory.store import MemoryStore
from financeiros.models import Decision, Side, utc_now


@dataclass
class CycleResult:
    cycle_id: int
    decisions: list[Decision]
    equity_usdt: float
    cash_usdt: float
    reserve_usdt: float
    total_wealth_usdt: float
    positions: dict[str, dict]
    reserve_skim_this_cycle: float


class TradingPipeline:
    """Orquestra: dados → sinal → risco → memória → (paper) execução → persistência."""

    def __init__(
        self,
        config: AppConfig,
        market: MarketDataService,
        signals: SignalEngine,
        risk: RiskEngine,
        portfolio: Portfolio,
        memory: MemoryStore,
        broker: PaperBroker,
    ):
        self.config = config
        self.market = market
        self.signals = signals
        self.risk = risk
        self.portfolio = portfolio
        self.memory = memory
        self.broker = broker

    def _skim_pct(self) -> float:
        capital = self.config.capital
        if not capital.reserve_enabled:
            return 0.0
        return float(capital.reserve_skim_pct)

    def run_once(self) -> CycleResult:
        if self.config.mode != "paper":
            raise RuntimeError(
                f"Modo '{self.config.mode}' ainda não suportado. Use paper."
            )

        started_at = utc_now()
        prices: dict[str, float] = {}
        decisions: list[Decision] = []
        skim_total = 0.0
        skim_pct = self._skim_pct()

        for symbol in self.config.universe.symbols:
            candles = self.market.get_candles(
                symbol=symbol,
                interval=self.config.universe.interval,
                limit=self.config.universe.lookback_candles,
            )
            signal = self.signals.evaluate(candles)
            prices[symbol] = signal.price

            snapshot = self.portfolio.mark_to_market(prices)
            assessment = self.risk.assess(signal, snapshot)

            memory_hints = self.memory.similar_rejected_patterns(symbol, signal.side)
            qty, notional = size_order(signal, assessment, self.config.capital)

            rationale_parts = [signal.rationale]
            if assessment.reasons:
                rationale_parts.append("Risco: " + "; ".join(assessment.reasons))
            if memory_hints:
                rationale_parts.append(
                    "Memória recente: " + " | ".join(memory_hints[:2])
                )

            decision = Decision(
                symbol=symbol,
                side=signal.side,
                approved=assessment.approved and qty > 0,
                rationale=" ".join(rationale_parts),
                signal_strength=signal.strength,
                price=signal.price,
                quantity=qty if assessment.approved else 0.0,
                notional=notional if assessment.approved else 0.0,
                tags=["paper", signal.side.value],
                metadata={
                    "volatility": signal.volatility,
                    "risk_reasons": assessment.reasons,
                    "signal_meta": signal.metadata,
                },
            )

            if decision.approved and signal.side == Side.BUY:
                vol_rejects = [h for h in memory_hints if "Volatilidade" in h]
                near_limit = signal.volatility > self.config.analysis.max_volatility * 0.9
                if vol_rejects and near_limit:
                    decision.approved = False
                    decision.quantity = 0.0
                    decision.notional = 0.0
                    decision.rationale += " Bloqueado por memória: volatilidade ainda elevada."
                    decision.tags.append("memory_block")

            if decision.approved and decision.side in (Side.BUY, Side.SELL):
                fill = self.broker.execute(
                    symbol=decision.symbol,
                    side=decision.side,
                    quantity=decision.quantity,
                    price=decision.price,
                )
                outcome = self.portfolio.apply_fill(fill, reserve_skim_pct=skim_pct)
                self.memory.record_fill(fill)
                decision.metadata["realized_pnl"] = outcome.realized_pnl
                decision.metadata["reserve_skim"] = outcome.reserve_skim
                if outcome.reserve_skim > 0:
                    skim_total += outcome.reserve_skim
                    decision.tags.append("reserve_skim")
                    decision.rationale += (
                        f" Reserva: +{outcome.reserve_skim:.4f} USDT "
                        f"({skim_pct:.0%} do lucro {outcome.realized_pnl:.4f})."
                    )
                    self.memory.record_reserve_transfer(
                        amount=outcome.reserve_skim,
                        realized_pnl=outcome.realized_pnl,
                        skim_pct=skim_pct,
                        symbol=decision.symbol,
                        note="Skim automático sobre lucro realizado",
                    )
                self.memory.save_portfolio(self.portfolio)

            decision_id = self.memory.record_decision(decision)
            decision.metadata["decision_id"] = decision_id
            decisions.append(decision)

        for symbol in self.config.universe.symbols:
            if symbol not in prices:
                prices[symbol] = self.market.get_price(symbol)

        final = self.portfolio.mark_to_market(prices)
        self.memory.save_portfolio(self.portfolio)
        finished_at = utc_now()
        cycle_id = self.memory.record_cycle(
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            cash_usdt=final.cash_usdt,
            equity_usdt=final.equity_usdt,
            decisions=decisions,
            reserve_usdt=final.reserve_usdt,
            total_wealth_usdt=final.total_wealth_usdt,
        )

        positions = {
            symbol: {
                "quantity": pos.quantity,
                "avg_price": pos.avg_price,
                "mark_price": prices.get(symbol, pos.avg_price),
            }
            for symbol, pos in self.portfolio.positions.items()
        }
        return CycleResult(
            cycle_id=cycle_id,
            decisions=decisions,
            equity_usdt=final.equity_usdt,
            cash_usdt=final.cash_usdt,
            reserve_usdt=final.reserve_usdt,
            total_wealth_usdt=final.total_wealth_usdt,
            positions=positions,
            reserve_skim_this_cycle=round(skim_total, 8),
        )
