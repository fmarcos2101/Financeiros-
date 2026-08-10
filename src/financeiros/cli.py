from __future__ import annotations

import argparse
import json
import sys
import time

from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.config import load_config
from financeiros.data.market import MarketDataService
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.execution.paper import PaperBroker
from financeiros.logging_setup import setup_logging
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
    portfolio = memory.load_portfolio(config.capital.starting_cash_usdt)
    return TradingPipeline(
        config=config,
        market=market,
        signals=SignalEngine(config.analysis),
        risk=RiskEngine(config.analysis, config.capital),
        portfolio=portfolio,
        memory=memory,
        broker=PaperBroker(config.execution),
    )


def _result_payload(result) -> dict:
    return {
        "cycle_id": result.cycle_id,
        "cash_usdt": result.cash_usdt,
        "equity_usdt": result.equity_usdt,
        "positions": result.positions,
        "decisions": [d.model_dump(mode="json") for d in result.decisions],
    }


def cmd_run_once(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logger = setup_logging(config.runtime.log_dir)
    pipeline = build_pipeline(args.config)
    result = pipeline.run_once()
    payload = _result_payload(result)
    logger.info(
        "cycle=%s cash=%.4f equity=%.4f approved=%s",
        result.cycle_id,
        result.cash_usdt,
        result.equity_usdt,
        [d.symbol for d in result.decisions if d.approved],
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_run_loop(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logger = setup_logging(config.runtime.log_dir)
    interval = args.interval or config.runtime.cycle_interval_seconds
    max_cycles = args.max_cycles
    pipeline = build_pipeline(args.config)

    logger.info(
        "Iniciando run-loop interval=%ss max_cycles=%s mode=%s",
        interval,
        max_cycles if max_cycles else "∞",
        config.mode,
    )

    cycle_n = 0
    try:
        while True:
            cycle_n += 1
            try:
                result = pipeline.run_once()
                logger.info(
                    "cycle_n=%s cycle_id=%s cash=%.4f equity=%.4f decisions=%s",
                    cycle_n,
                    result.cycle_id,
                    result.cash_usdt,
                    result.equity_usdt,
                    len(result.decisions),
                )
                print(json.dumps(_result_payload(result), indent=2, ensure_ascii=False))
            except Exception:
                logger.exception("Falha no ciclo %s — tentará novamente no próximo intervalo", cycle_n)

            if max_cycles is not None and cycle_n >= max_cycles:
                logger.info("Máximo de ciclos atingido (%s). Encerrando.", max_cycles)
                break

            logger.info("Aguardando %ss até o próximo ciclo...", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário após %s ciclo(s).", cycle_n)
        return 0

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


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    memory = MemoryStore(config.memory.db_path)
    status = memory.get_status()
    # Enriquece com mark-to-market se houver posições
    if status["positions"]:
        try:
            client = BinancePublicClient(
                base_url=config.exchange.base_url,
                timeout_seconds=config.exchange.timeout_seconds,
            )
            equity = status["cash_usdt"] or 0.0
            enriched = []
            for pos in status["positions"]:
                px = client.fetch_price(pos["symbol"])
                mtm = pos["quantity"] * px
                equity += mtm
                enriched.append({**pos, "mark_price": px, "market_value": round(mtm, 6)})
            status["positions"] = enriched
            status["equity_usdt"] = round(equity, 6)
        except Exception as exc:
            status["equity_usdt"] = None
            status["mark_to_market_error"] = str(exc)
    else:
        status["equity_usdt"] = status["cash_usdt"]
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


def cmd_cycles(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    memory = MemoryStore(config.memory.db_path)
    rows = memory.recent_cycles(limit=args.limit)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def cmd_reset_portfolio(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not args.yes:
        print(
            "Confirme com --yes para zerar posições e restaurar caixa inicial. "
            "Histórico de decisões/ciclos é mantido.",
            file=sys.stderr,
        )
        return 2
    memory = MemoryStore(config.memory.db_path)
    cash = args.cash if args.cash is not None else config.capital.starting_cash_usdt
    memory.reset_portfolio(cash)
    print(json.dumps({"reset": True, "cash_usdt": cash}, indent=2))
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

    loop = sub.add_parser("run-loop", help="Roda ciclos continuamente com intervalo")
    loop.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Segundos entre ciclos (default: config runtime.cycle_interval_seconds)",
    )
    loop.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Para após N ciclos (útil para teste)",
    )
    loop.set_defaults(func=cmd_run_loop)

    status = sub.add_parser("status", help="Mostra caixa, posições e último ciclo")
    status.set_defaults(func=cmd_status)

    cycles = sub.add_parser("cycles", help="Lista ciclos recentes")
    cycles.add_argument("--limit", type=int, default=10)
    cycles.set_defaults(func=cmd_cycles)

    mem = sub.add_parser("memory", help="Lista decisões recentes na memória")
    mem.add_argument("--symbol", default=None)
    mem.add_argument("--limit", type=int, default=20)
    mem.set_defaults(func=cmd_show_memory)

    lesson = sub.add_parser("lesson", help="Registra uma lição aprendida")
    lesson.add_argument("text")
    lesson.add_argument("--symbol", default=None)
    lesson.set_defaults(func=cmd_add_lesson)

    reset = sub.add_parser(
        "reset-portfolio",
        help="Zera posições e restaura caixa (mantém histórico)",
    )
    reset.add_argument("--yes", action="store_true", help="Confirma o reset")
    reset.add_argument("--cash", type=float, default=None, help="Caixa após reset")
    reset.set_defaults(func=cmd_reset_portfolio)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args.func(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
