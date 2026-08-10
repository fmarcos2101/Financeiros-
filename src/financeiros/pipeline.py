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
from financeiros.models import Decision, Side


@dataclass
class CycleResult:
    decisions: list[Decision]
    equity_usdt: float
    cash_usdt: float


class TradingPipeline:
    """Orquestra: dados → sinal → risco → memória → (paper) execução."""

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

    def run_once(self) -> CycleResult:
        if self.config.mode != "paper":
            raise RuntimeError(
                f"Modo '{self.config.mode}' ainda não suportado. Use paper."
            )

        prices: dict[str, float] = {}
        decisions: list[Decision] = []

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

            # Reforço leve via memória: se rejeições recentes foram por volatilidade
            # e o mercado ainda está perto do limite, não insiste na compra.
            if decision.approved and signal.side == Side.BUY:
                vol_rejects = [h for h in memory_hints if "Volatilidade" in h]
                near_limit = signal.volatility > self.config.analysis.max_volatility * 0.9
                if vol_rejects and near_limit:
                    decision.approved = False
                    decision.quantity = 0.0
                    decision.notional = 0.0
                    decision.rationale += " Bloqueado por memória: volatilidade ainda elevada."
                    decision.tags.append("memory_block")

            decision_id = self.memory.record_decision(decision)
            decision.metadata["decision_id"] = decision_id

            if decision.approved and decision.side in (Side.BUY, Side.SELL):
                fill = self.broker.execute(
                    symbol=decision.symbol,
                    side=decision.side,
                    quantity=decision.quantity,
                    price=decision.price,
                )
                self.portfolio.apply_fill(fill)
                self.memory.record_fill(fill)

            decisions.append(decision)

        # Atualiza preços faltantes para MTM
        for symbol in self.config.universe.symbols:
            if symbol not in prices:
                prices[symbol] = self.market.get_price(symbol)
        final = self.portfolio.mark_to_market(prices)
        return CycleResult(
            decisions=decisions,
            equity_usdt=final.equity_usdt,
            cash_usdt=final.cash_usdt,
        )
