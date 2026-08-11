from __future__ import annotations

from dataclasses import dataclass

from financeiros.analysis.circuit import CircuitBreaker, CircuitSnapshot
from financeiros.analysis.exits import ExitAdvice, ExitEngine
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
    exits_this_cycle: int
    circuit: dict


class TradingPipeline:
    """Orquestra: dados → circuit → saídas → sinal → risco → paper → persistência."""

    def __init__(
        self,
        config: AppConfig,
        market: MarketDataService,
        signals: SignalEngine,
        risk: RiskEngine,
        portfolio: Portfolio,
        memory: MemoryStore,
        broker: PaperBroker,
        exits: ExitEngine | None = None,
        circuit: CircuitBreaker | None = None,
    ):
        self.config = config
        self.market = market
        self.signals = signals
        self.risk = risk
        self.portfolio = portfolio
        self.memory = memory
        self.broker = broker
        self.exits = exits or ExitEngine(config.exits)
        self.circuit = circuit or CircuitBreaker(config.circuit_breaker)

    def _skim_pct(self) -> float:
        capital = self.config.capital
        if not capital.reserve_enabled:
            return 0.0
        return float(capital.reserve_skim_pct)

    def _apply_approved_trade(self, decision: Decision, skim_pct: float) -> float:
        """Executa fill + reserva. Retorna valor skimado."""
        skimmed = 0.0
        if not (decision.approved and decision.side in (Side.BUY, Side.SELL)):
            return skimmed

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
            skimmed = outcome.reserve_skim
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
        return skimmed

    def _decision_from_exit(self, advice: ExitAdvice) -> Decision:
        return Decision(
            symbol=advice.symbol,
            side=Side.SELL,
            approved=True,
            rationale=advice.rationale,
            signal_strength=1.0,
            price=advice.fill_price,
            quantity=advice.quantity,
            notional=round(advice.quantity * advice.fill_price, 6),
            tags=["paper", "exit", advice.reason, "sell"],
            metadata={
                "exit_reason": advice.reason,
                "trigger_price": advice.trigger_price,
                "pnl_pct": advice.pnl_pct,
                "forced_exit": True,
            },
        )

    def _sync_circuit(self, wealth_usdt: float) -> CircuitSnapshot:
        snap = self.circuit.evaluate(
            wealth_usdt=wealth_usdt,
            now=utc_now(),
            state=self.memory.get_robot_state(),
        )
        self.memory.save_circuit_state(
            halted=snap.halted,
            reason=snap.reason,
            halted_at=snap.halted_at,
            day_anchor_date=snap.day_anchor_date,
            day_start_wealth=snap.day_start_wealth,
            week_anchor_date=snap.week_anchor_date,
            week_start_wealth=snap.week_start_wealth,
        )
        if snap.newly_tripped:
            self.memory.add_lesson(
                lesson=f"Circuit breaker ativado: {snap.reason}",
                tags=["auto", "circuit_breaker"],
            )
        return snap

    def run_once(self) -> CycleResult:
        if self.config.mode != "paper":
            raise RuntimeError(
                f"Modo '{self.config.mode}' ainda não suportado. Use paper."
            )

        started_at = utc_now()
        prices: dict[str, float] = {}
        candles_by_symbol: dict[str, list] = {}
        decisions: list[Decision] = []
        skim_total = 0.0
        exits_count = 0
        skim_pct = self._skim_pct()

        # Prefetch para MTM + circuit breaker antes das entradas
        for symbol in self.config.universe.symbols:
            candles = self.market.get_candles(
                symbol=symbol,
                interval=self.config.universe.interval,
                limit=self.config.universe.lookback_candles,
            )
            candles_by_symbol[symbol] = candles
            prices[symbol] = candles[-1].close if candles else self.market.get_price(symbol)

        pre_snap = self.portfolio.mark_to_market(prices)
        circuit_snap = self._sync_circuit(pre_snap.total_wealth_usdt)
        block_entries = (
            circuit_snap.halted
            and self.config.circuit_breaker.enabled
            and self.config.circuit_breaker.block_new_entries
        )
        allow_exits = (
            (not circuit_snap.halted)
            or self.config.circuit_breaker.allow_exits
            or not self.config.circuit_breaker.enabled
        )

        for symbol in self.config.universe.symbols:
            candles = candles_by_symbol[symbol]

            # 1) Saídas automáticas (permitidas mesmo com circuit breaker)
            position = self.portfolio.positions.get(symbol)
            if position and position.quantity > 0:
                advice = self.exits.evaluate(position, candles) if allow_exits else None
                self.memory.save_portfolio(self.portfolio)
                if advice is not None:
                    decision = self._decision_from_exit(advice)
                    skim_total += self._apply_approved_trade(decision, skim_pct)
                    if advice.reason in {"stop_loss", "trailing_stop"}:
                        self.memory.add_lesson(
                            lesson=(
                                f"{advice.reason} em {symbol}: pnl {advice.pnl_pct:.2%}. "
                                "Reavaliar entrada em volatilidade semelhante."
                            ),
                            symbol=symbol,
                            tags=["auto", advice.reason],
                        )
                    decision_id = self.memory.record_decision(decision)
                    decision.metadata["decision_id"] = decision_id
                    decisions.append(decision)
                    exits_count += 1
                    continue

            # 2) Fluxo normal de sinal
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

            # Circuit breaker: bloqueia novas compras
            if decision.approved and decision.side == Side.BUY and block_entries:
                decision.approved = False
                decision.quantity = 0.0
                decision.notional = 0.0
                decision.rationale += (
                    f" Bloqueado por circuit breaker ({circuit_snap.reason})."
                )
                decision.tags.append("circuit_block")
                decision.metadata["circuit_halted"] = True

            skim_total += self._apply_approved_trade(decision, skim_pct)
            decision_id = self.memory.record_decision(decision)
            decision.metadata["decision_id"] = decision_id
            decisions.append(decision)

        for symbol in self.config.universe.symbols:
            if symbol not in prices:
                prices[symbol] = self.market.get_price(symbol)

        final = self.portfolio.mark_to_market(prices)
        self.memory.save_portfolio(self.portfolio)
        # Reavalia circuit com wealth final do ciclo
        circuit_final = self._sync_circuit(final.total_wealth_usdt)
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

        positions = {}
        for symbol, pos in self.portfolio.positions.items():
            hard_stop = pos.avg_price * (1 - self.config.exits.stop_loss_pct)
            trail_stop = None
            peak = pos.peak_price or pos.avg_price
            if self.config.exits.trailing_enabled and peak > 0:
                gain = (peak - pos.avg_price) / pos.avg_price
                if gain >= self.config.exits.trailing_activation_pct:
                    trail_stop = peak * (1 - self.config.exits.trailing_pct)
            positions[symbol] = {
                "quantity": pos.quantity,
                "avg_price": pos.avg_price,
                "peak_price": peak,
                "mark_price": prices.get(symbol, pos.avg_price),
                "stop_loss": round(hard_stop, 8) if self.config.exits.enabled else None,
                "trailing_stop": round(trail_stop, 8) if trail_stop is not None else None,
                "take_profit": round(pos.avg_price * (1 + self.config.exits.take_profit_pct), 8)
                if self.config.exits.enabled
                else None,
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
            exits_this_cycle=exits_count,
            circuit=circuit_final.to_dict(),
        )
