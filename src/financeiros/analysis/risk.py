from __future__ import annotations

from financeiros.config import AnalysisConfig, CapitalConfig
from financeiros.models import PortfolioSnapshot, RiskAssessment, Side, Signal


class RiskEngine:
    """Filtros de risco antes de qualquer execução."""

    def __init__(self, analysis: AnalysisConfig, capital: CapitalConfig):
        self.analysis = analysis
        self.capital = capital

    def assess(self, signal: Signal, portfolio: PortfolioSnapshot) -> RiskAssessment:
        reasons: list[str] = []

        if signal.side == Side.HOLD:
            reasons.append("Sinal é HOLD.")
            return RiskAssessment(symbol=signal.symbol, approved=False, reasons=reasons)

        if signal.volatility > self.analysis.max_volatility:
            reasons.append(
                f"Volatilidade {signal.volatility:.4f} acima do limite "
                f"{self.analysis.max_volatility:.4f}."
            )

        open_positions = sum(1 for p in portfolio.positions.values() if p.quantity > 0)
        has_position = portfolio.positions.get(signal.symbol, None)
        already_in = bool(has_position and has_position.quantity > 0)

        if signal.side == Side.BUY:
            if already_in:
                reasons.append("Já existe posição aberta neste símbolo.")
            if open_positions >= self.capital.max_open_positions and not already_in:
                reasons.append(
                    f"Limite de posições abertas atingido ({self.capital.max_open_positions})."
                )
            if portfolio.cash_usdt < self.capital.min_notional_usdt:
                reasons.append("Caixa insuficiente para o mínimo notional.")

        if signal.side == Side.SELL:
            if not already_in:
                reasons.append("Não há posição para vender.")

        equity = portfolio.equity_usdt or portfolio.cash_usdt
        risk_budget = equity * self.capital.risk_per_trade
        max_by_pct = equity * self.capital.max_position_pct
        max_notional = min(risk_budget / max(signal.volatility, 0.01), max_by_pct, portfolio.cash_usdt)

        if signal.side == Side.BUY and max_notional < self.capital.min_notional_usdt:
            reasons.append(
                f"Notional sugerido ({max_notional:.2f}) abaixo do mínimo "
                f"({self.capital.min_notional_usdt:.2f})."
            )

        approved = len(reasons) == 0
        qty = 0.0
        if approved and signal.side == Side.BUY and signal.price > 0:
            qty = max_notional / signal.price
        elif approved and signal.side == Side.SELL and has_position:
            qty = has_position.quantity
            max_notional = qty * signal.price

        return RiskAssessment(
            symbol=signal.symbol,
            approved=approved,
            reasons=reasons,
            max_notional=round(max_notional if approved else 0.0, 6),
            suggested_qty=round(qty, 8),
        )
