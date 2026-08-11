from financeiros.analysis.risk import RiskEngine
from financeiros.capital.portfolio import Portfolio
from financeiros.capital.sizing import size_order
from financeiros.config import AnalysisConfig, CapitalConfig
from financeiros.execution.paper import PaperBroker
from financeiros.config import ExecutionConfig
from financeiros.models import PortfolioSnapshot, Side, Signal


def test_risk_blocks_hold():
    risk = RiskEngine(AnalysisConfig(), CapitalConfig())
    signal = Signal(
        symbol="BTCUSDT",
        side=Side.HOLD,
        strength=0.0,
        rationale="hold",
        price=50000,
        volatility=0.01,
    )
    assessment = risk.assess(signal, PortfolioSnapshot(cash_usdt=1000, equity_usdt=1000))
    assert assessment.approved is False


def test_risk_and_sizing_buy():
    capital = CapitalConfig(
        starting_cash_usdt=1000,
        risk_per_trade=0.02,
        max_position_pct=0.25,
        min_notional_usdt=10,
    )
    risk = RiskEngine(AnalysisConfig(max_volatility=0.1), capital)
    signal = Signal(
        symbol="BTCUSDT",
        side=Side.BUY,
        strength=0.5,
        rationale="cross",
        price=100.0,
        volatility=0.02,
    )
    assessment = risk.assess(signal, PortfolioSnapshot(cash_usdt=1000, equity_usdt=1000))
    assert assessment.approved is True
    qty, notional = size_order(signal, assessment, capital)
    assert qty > 0
    assert notional >= capital.min_notional_usdt


def test_paper_broker_and_portfolio_roundtrip():
    broker = PaperBroker(ExecutionConfig(fee_bps=10))
    portfolio = Portfolio(1000.0)
    buy = broker.execute("ETHUSDT", Side.BUY, quantity=1.0, price=100.0)
    portfolio.apply_fill(buy)
    assert portfolio.cash_usdt < 1000
    assert "ETHUSDT" in portfolio.positions

    sell = broker.execute("ETHUSDT", Side.SELL, quantity=1.0, price=110.0)
    outcome = portfolio.apply_fill(sell, reserve_skim_pct=0.0)
    assert "ETHUSDT" not in portfolio.positions
    assert portfolio.cash_usdt > 900
    assert outcome.reserve_skim == 0.0
