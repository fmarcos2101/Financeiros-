from __future__ import annotations

import argparse
import json
import sys

from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.capital.portfolio import Portfolio
from financeiros.config import load_config
from financeiros.data.market import MarketDataService
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.execution.paper import PaperBroker
from financeiros.memory.store import MemoryStore
from financeiros.pipeline import TradingPipeline


def build_pipeline(config_path: str | None = None) -> TradingPipeline:
    config = load_config(config_path)
    client = BinancePublicClient(
        base_url=config.exchange.base_url,
        timeout_seconds=config.exchange.timeout_seconds,
    )
    market = MarketDataService(client)
    memory = MemoryStore(config.memory.db_path)
    portfolio = Portfolio(config.capital.starting_cash_usdt)
    return TradingPipeline(
        config=config,
        market=market,
        signals=SignalEngine(config.analysis),
        risk=RiskEngine(config.analysis, config.capital),
        portfolio=portfolio,
        memory=memory,
        broker=PaperBroker(config.execution),
    )


def cmd_run_once(args: argparse.Namespace) -> int:
    pipeline = build_pipeline(args.config)
    result = pipeline.run_once()
    payload = {
        "cash_usdt": result.cash_usdt,
        "equity_usdt": result.equity_usdt,
        "decisions": [d.model_dump(mode="json") for d in result.decisions],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_show_memory(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    memory = MemoryStore(config.memory.db_path)
    rows = memory.recent_decisions(symbol=args.symbol, limit=args.limit)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def cmd_add_lesson(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    memory = MemoryStore(config.memory.db_path)
    lesson_id = memory.add_lesson(lesson=args.text, symbol=args.symbol, tags=["manual"])
    print(json.dumps({"lesson_id": lesson_id}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="financeiros",
        description="Robô pessoal de análise/risco/paper trading para cripto.",
    )
    parser.add_argument("--config", default=None, help="Caminho do YAML de config")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run-once", help="Roda um ciclo de análise + paper trading")
    run.set_defaults(func=cmd_run_once)

    mem = sub.add_parser("memory", help="Lista decisões recentes na memória")
    mem.add_argument("--symbol", default=None)
    mem.add_argument("--limit", type=int, default=20)
    mem.set_defaults(func=cmd_show_memory)

    lesson = sub.add_parser("lesson", help="Registra uma lição aprendida")
    lesson.add_argument("text")
    lesson.add_argument("--symbol", default=None)
    lesson.set_defaults(func=cmd_add_lesson)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args.func(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
