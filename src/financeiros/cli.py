from __future__ import annotations

import argparse
import json
import sys
import time

from pathlib import Path

from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.backtest import BacktestEngine
from financeiros.config import load_config
from financeiros.tune import StrategyTuner
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
        "reserve_usdt": result.reserve_usdt,
        "equity_usdt": result.equity_usdt,
        "total_wealth_usdt": result.total_wealth_usdt,
        "reserve_skim_this_cycle": result.reserve_skim_this_cycle,
        "exits_this_cycle": result.exits_this_cycle,
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
        "cycle=%s cash=%.4f reserve=%.4f equity=%.4f wealth=%.4f approved=%s",
        result.cycle_id,
        result.cash_usdt,
        result.reserve_usdt,
        result.equity_usdt,
        result.total_wealth_usdt,
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
                    "cycle_n=%s cycle_id=%s cash=%.4f reserve=%.4f equity=%.4f wealth=%.4f decisions=%s",
                    cycle_n,
                    result.cycle_id,
                    result.cash_usdt,
                    result.reserve_usdt,
                    result.equity_usdt,
                    result.total_wealth_usdt,
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
    reserve = status.get("reserve_usdt") or 0.0
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
            status["total_wealth_usdt"] = round(equity + reserve, 6)
        except Exception as exc:
            status["equity_usdt"] = None
            status["total_wealth_usdt"] = None
            status["mark_to_market_error"] = str(exc)
    else:
        cash = status["cash_usdt"] or 0.0
        status["equity_usdt"] = cash
        status["total_wealth_usdt"] = round(cash + reserve, 6)
    status["reserve_skim_pct"] = config.capital.reserve_skim_pct
    status["reserve_enabled"] = config.capital.reserve_enabled
    status["exits"] = {
        "enabled": config.exits.enabled,
        "stop_loss_pct": config.exits.stop_loss_pct,
        "take_profit_pct": config.exits.take_profit_pct,
        "trailing_enabled": config.exits.trailing_enabled,
        "trailing_pct": config.exits.trailing_pct,
        "trailing_activation_pct": config.exits.trailing_activation_pct,
    }
    if status["positions"] and config.exits.enabled:
        enriched_pos = []
        for pos in status["positions"]:
            avg = float(pos["avg_price"])
            peak = float(pos.get("peak_price") or avg)
            trail = None
            if config.exits.trailing_enabled and peak > 0:
                gain = (peak - avg) / avg if avg else 0.0
                if gain >= config.exits.trailing_activation_pct:
                    trail = round(peak * (1 - config.exits.trailing_pct), 8)
            enriched_pos.append(
                {
                    **pos,
                    "peak_price": peak,
                    "stop_loss": round(avg * (1 - config.exits.stop_loss_pct), 8),
                    "trailing_stop": trail,
                    "take_profit": round(avg * (1 + config.exits.take_profit_pct), 8),
                }
            )
        status["positions"] = enriched_pos
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0


def cmd_reserve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    memory = MemoryStore(config.memory.db_path)
    status = memory.get_status()
    transfers = memory.recent_reserve_transfers(limit=args.limit)
    print(
        json.dumps(
            {
                "reserve_usdt": status.get("reserve_usdt"),
                "reserve_skimmed_total": status.get("reserve_skimmed_total"),
                "reserve_enabled": config.capital.reserve_enabled,
                "reserve_skim_pct": config.capital.reserve_skim_pct,
                "transfers": transfers,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_cycles(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    memory = MemoryStore(config.memory.db_path)
    rows = memory.recent_cycles(limit=args.limit)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logger = setup_logging(config.runtime.log_dir)
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else list(config.universe.symbols)
    )
    interval = args.interval or config.universe.interval
    tuner = StrategyTuner(config)
    logger.info(
        "Tune start symbols=%s interval=%s days=%s",
        symbols,
        interval,
        args.days,
    )
    result = tuner.run(
        days=args.days,
        symbols=symbols,
        interval=interval,
        max_dd_limit=args.max_dd,
        top_n=args.top,
    )
    best = result.get("best") or {}
    logger.info(
        "Tune done tested=%s best_return=%s best_dd=%s trailing=%s",
        result.get("tested"),
        best.get("total_return_pct"),
        best.get("max_drawdown_pct"),
        best.get("trailing_enabled"),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Tune salvo em %s", out)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logger = setup_logging(config.runtime.log_dir)
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else list(config.universe.symbols)
    )
    interval = args.interval or config.universe.interval
    engine = BacktestEngine(config)
    logger.info(
        "Backtest start symbols=%s interval=%s days=%s",
        symbols,
        interval,
        args.days,
    )
    report = engine.run(days=args.days, symbols=symbols, interval=interval)
    payload = report.to_dict(include_trades=args.trades, include_curve=args.curve)
    logger.info(
        "Backtest done return=%.2f%% max_dd=%.2f%% trades=%s wealth=%.2f",
        report.total_return_pct,
        report.max_drawdown_pct,
        report.trades,
        report.ending_wealth_usdt,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Relatório salvo em %s", out)
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
    memory.reset_portfolio(cash, keep_reserve=args.keep_reserve)
    status = memory.get_status()
    print(
        json.dumps(
            {
                "reset": True,
                "cash_usdt": cash,
                "reserve_usdt": status.get("reserve_usdt"),
                "keep_reserve": args.keep_reserve,
            },
            indent=2,
        )
    )
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

    status = sub.add_parser("status", help="Mostra caixa, reserva, posições e último ciclo")
    status.set_defaults(func=cmd_status)

    reserve = sub.add_parser("reserve", help="Mostra fundo reserva e transferências")
    reserve.add_argument("--limit", type=int, default=20)
    reserve.set_defaults(func=cmd_reserve)

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
    reset.add_argument(
        "--keep-reserve",
        action="store_true",
        help="Mantém o fundo reserva ao resetar o capital de trading",
    )
    reset.set_defaults(func=cmd_reset_portfolio)

    bt = sub.add_parser("backtest", help="Replay offline da estratégia no histórico")
    bt.add_argument("--days", type=int, default=60, help="Janela histórica em dias")
    bt.add_argument(
        "--symbols",
        default=None,
        help="Lista separada por vírgula (default: config universe)",
    )
    bt.add_argument(
        "--interval",
        default=None,
        help="Intervalo dos candles (default: config universe.interval)",
    )
    bt.add_argument("--trades", action="store_true", help="Inclui log de trades no JSON")
    bt.add_argument("--curve", action="store_true", help="Inclui curva de equity no JSON")
    bt.add_argument(
        "--save",
        default=None,
        help="Salva o relatório JSON neste caminho",
    )
    bt.set_defaults(func=cmd_backtest)

    tune = sub.add_parser("tune", help="Busca parâmetros de saída via grid no histórico")
    tune.add_argument("--days", type=int, default=60, help="Janela histórica em dias")
    tune.add_argument("--symbols", default=None, help="Lista separada por vírgula")
    tune.add_argument("--interval", default=None, help="Intervalo dos candles")
    tune.add_argument(
        "--max-dd",
        type=float,
        default=8.0,
        help="Penaliza drawdown acima deste %%",
    )
    tune.add_argument("--top", type=int, default=5, help="Quantos melhores mostrar")
    tune.add_argument("--save", default=None, help="Salva resultado JSON")
    tune.set_defaults(func=cmd_tune)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args.func(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
