from __future__ import annotations

import argparse
import json
import sys
import time

from pathlib import Path

from financeiros.analysis.risk import RiskEngine
from financeiros.analysis.signals import SignalEngine
from financeiros.backtest import BacktestEngine
from financeiros.config import get_settings, load_config
from financeiros.tune import StrategyTuner
from financeiros.data.market import MarketDataService
from financeiros.data.providers.binance import BinancePublicClient
from financeiros.data.providers.binance_trading import (
    LIVE_TRADING_URL,
    TESTNET_TRADING_URL,
    BinanceTradingClient,
)
from financeiros.execution.live import LiveBroker
from financeiros.execution.paper import PaperBroker
from financeiros.logging_setup import setup_logging
from financeiros.memory.store import MemoryStore
from financeiros.dashboard import run_dashboard
from financeiros.health import read_health, write_heartbeat
from financeiros.pipeline import TradingPipeline
from financeiros.report import DailyReporter
from financeiros.validate import OutOfSampleValidator


def _trading_base_url(config) -> str:
    if config.exchange.trading_base_url:
        return config.exchange.trading_base_url
    if config.mode == "live":
        return LIVE_TRADING_URL
    return TESTNET_TRADING_URL


def build_broker(config):
    if config.mode == "paper":
        return PaperBroker(config.execution)
    if config.mode in {"testnet", "live"}:
        settings = get_settings()
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise RuntimeError(
                "Modo testnet/live exige BINANCE_API_KEY e BINANCE_API_SECRET no .env"
            )
        trading = BinanceTradingClient(
            settings.binance_api_key,
            settings.binance_api_secret,
            base_url=_trading_base_url(config),
            timeout_seconds=config.exchange.timeout_seconds,
            recv_window_ms=config.execution.recv_window_ms,
        )
        return LiveBroker(config.execution, trading, mode=config.mode)
    raise RuntimeError(f"Modo não suportado: {config.mode}")


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
        broker=build_broker(config),
    )


def _ensure_live_cli_allowed(config, args: argparse.Namespace) -> None:
    """Bloqueia testnet/live sem confirmação explícita na CLI."""
    if config.mode == "paper":
        return
    if config.mode == "testnet" and not getattr(args, "confirm_testnet", False):
        raise SystemExit(
            "Modo testnet: confirme com --confirm-testnet "
            "(ainda pode estar em dry_run)."
        )
    if config.mode == "live" and not getattr(args, "confirm_live", False):
        raise SystemExit(
            "Modo LIVE: confirme com --confirm-live. "
            "Use dry_run=true até validar; keys sem permissão de saque."
        )
    if (
        config.mode == "live"
        and not config.execution.dry_run
        and not getattr(args, "confirm_live_orders", False)
    ):
        raise SystemExit(
            "Modo LIVE com dry_run=false: confirme também com "
            "--confirm-live-orders (ordens reais)."
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
        "circuit": result.circuit,
        "positions": result.positions,
        "decisions": [d.model_dump(mode="json") for d in result.decisions],
    }


def cmd_run_once(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    _ensure_live_cli_allowed(config, args)
    logger = setup_logging(config.runtime.log_dir)
    logger.info(
        "mode=%s dry_run=%s max_order_notional=%s",
        config.mode,
        config.execution.dry_run,
        config.execution.max_order_notional_usdt,
    )
    pipeline = build_pipeline(args.config)
    try:
        result = pipeline.run_once()
    except Exception as exc:
        write_heartbeat(
            config.runtime.log_dir,
            status="error",
            detail=str(exc),
        )
        raise
    payload = _result_payload(result)
    write_heartbeat(
        config.runtime.log_dir,
        cycle_id=result.cycle_id,
        wealth_usdt=result.total_wealth_usdt,
        halted=bool(result.circuit.get("halted")),
        status="ok",
    )
    logger.info(
        "cycle=%s cash=%.4f reserve=%.4f equity=%.4f wealth=%.4f halted=%s approved=%s",
        result.cycle_id,
        result.cash_usdt,
        result.reserve_usdt,
        result.equity_usdt,
        result.total_wealth_usdt,
        result.circuit.get("halted"),
        [d.symbol for d in result.decisions if d.approved],
    )
    if result.circuit.get("halted"):
        logger.warning("Circuit breaker ATIVO: %s", result.circuit.get("reason"))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_run_loop(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    _ensure_live_cli_allowed(config, args)
    logger = setup_logging(config.runtime.log_dir)
    interval = args.interval or config.runtime.cycle_interval_seconds
    max_cycles = args.max_cycles
    pipeline = build_pipeline(args.config)
    reporter = DailyReporter(config, memory=pipeline.memory)

    logger.info(
        "Iniciando run-loop interval=%ss max_cycles=%s mode=%s dry_run=%s",
        interval,
        max_cycles if max_cycles else "∞",
        config.mode,
        config.execution.dry_run,
    )

    cycle_n = 0
    try:
        while True:
            cycle_n += 1
            try:
                if config.runtime.emit_daily_report_in_loop:
                    emitted = reporter.maybe_emit_for_new_day()
                    if emitted is not None:
                        logger.info(
                            "Relatório diário emitido %s alerts=%s",
                            emitted.date,
                            len(emitted.alerts),
                        )
                        for alert in emitted.alerts:
                            log_fn = logger.warning if alert.level != "info" else logger.info
                            log_fn("ALERT [%s] %s: %s", alert.level, alert.code, alert.message)

                result = pipeline.run_once()
                write_heartbeat(
                    config.runtime.log_dir,
                    cycle_id=result.cycle_id,
                    cycle_n=cycle_n,
                    wealth_usdt=result.total_wealth_usdt,
                    halted=bool(result.circuit.get("halted")),
                    status="ok",
                )
                logger.info(
                    "cycle_n=%s cycle_id=%s cash=%.4f reserve=%.4f equity=%.4f wealth=%.4f halted=%s decisions=%s",
                    cycle_n,
                    result.cycle_id,
                    result.cash_usdt,
                    result.reserve_usdt,
                    result.equity_usdt,
                    result.total_wealth_usdt,
                    result.circuit.get("halted"),
                    len(result.decisions),
                )
                if result.circuit.get("halted"):
                    logger.warning(
                        "Circuit breaker ativo — novas compras bloqueadas (%s)",
                        result.circuit.get("reason"),
                    )
                print(json.dumps(_result_payload(result), indent=2, ensure_ascii=False))
            except Exception as exc:
                write_heartbeat(
                    config.runtime.log_dir,
                    cycle_n=cycle_n,
                    status="error",
                    detail=str(exc),
                )
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


def cmd_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logger = setup_logging(config.runtime.log_dir)
    reporter = DailyReporter(config)
    report = reporter.build(args.date)
    path = reporter.save(report)
    logger.info(
        "Relatório %s salvo em %s | alerts=%s wealth=%s daily_pnl=%s%%",
        report.date,
        path,
        len(report.alerts),
        report.summary.get("ending_wealth_usdt"),
        report.summary.get("daily_pnl_pct"),
    )
    for alert in report.alerts:
        log_fn = logger.warning if alert.level != "info" else logger.info
        log_fn("ALERT [%s] %s: %s", alert.level, alert.code, alert.message)

    if args.text:
        print(report.to_text())
    else:
        payload = report.to_dict()
        if not args.full:
            payload.pop("decisions", None)
            payload.pop("fills", None)
            payload.pop("cycles", None)
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
    status["circuit_breaker_config"] = {
        "enabled": config.circuit_breaker.enabled,
        "max_daily_loss_pct": config.circuit_breaker.max_daily_loss_pct,
        "max_weekly_loss_pct": config.circuit_breaker.max_weekly_loss_pct,
        "block_new_entries": config.circuit_breaker.block_new_entries,
        "allow_exits": config.circuit_breaker.allow_exits,
        "auto_resume_next_day": config.circuit_breaker.auto_resume_next_day,
    }
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


def cmd_health(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    health = read_health(
        config.runtime.log_dir,
        stale_after_seconds=config.runtime.heartbeat_stale_seconds,
    )
    print(json.dumps(health, indent=2, ensure_ascii=False))
    if health.get("alive"):
        return 0
    return 1


def cmd_dashboard(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    host = args.host or config.runtime.dashboard_host
    port = args.port or config.runtime.dashboard_port
    logger = setup_logging(config.runtime.log_dir)
    logger.info("Dashboard em http://%s:%s (Ctrl+C para sair)", host, port)
    run_dashboard(config, host=host, port=port)
    return 0


def cmd_live_check(args: argparse.Namespace) -> int:
    """Valida keys/conectividade sem colocar ordem."""
    config = load_config(args.config)
    if args.mode:
        config.mode = args.mode
    if config.mode == "paper":
        config.mode = "testnet"
    settings = get_settings()
    if not settings.binance_api_key or not settings.binance_api_secret:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "BINANCE_API_KEY / BINANCE_API_SECRET ausentes no .env",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1

    client = BinanceTradingClient(
        settings.binance_api_key,
        settings.binance_api_secret,
        base_url=_trading_base_url(config),
        timeout_seconds=config.exchange.timeout_seconds,
        recv_window_ms=config.execution.recv_window_ms,
    )
    try:
        client.ping()
        acct = client.account()
        balances = {
            b["asset"]: float(b["free"])
            for b in acct.get("balances", [])
            if float(b.get("free") or 0) > 0
        }
        top = dict(sorted(balances.items(), key=lambda kv: kv[1], reverse=True)[:12])
        payload = {
            "ok": True,
            "mode": config.mode,
            "trading_base_url": _trading_base_url(config),
            "can_trade": acct.get("canTrade"),
            "account_type": acct.get("accountType"),
            "balances_free": top,
            "dry_run_config": config.execution.dry_run,
            "max_order_notional_usdt": config.execution.max_order_notional_usdt,
            "note": "Nenhuma ordem foi enviada. live-check é somente leitura.",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": config.mode,
                    "trading_base_url": _trading_base_url(config),
                    "error": str(exc),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 1


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logger = setup_logging(config.runtime.log_dir)
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else list(config.universe.symbols)
    )
    interval = args.interval or config.universe.interval
    validator = OutOfSampleValidator(config)
    common = dict(
        train_days=args.train_days,
        holdout_days=args.holdout_days,
        symbols=symbols,
        interval=interval,
        tune=not args.no_tune,
        max_dd_limit=args.max_dd,
        min_holdout_return_pct=args.min_return,
        max_holdout_dd_pct=args.max_holdout_dd,
        min_holdout_trades=args.min_trades,
        max_return_drop_pct=args.max_drop,
    )
    if args.walk_forward:
        logger.info(
            "OOS walk-forward folds=%s train=%sd holdout=%sd tune=%s symbols=%s",
            args.folds,
            args.train_days,
            args.holdout_days,
            not args.no_tune,
            symbols,
        )
        result = validator.run_walk_forward(folds=args.folds, **common)
        logger.info(
            "OOS walk-forward verdict=%s passes=%s/%s avg_holdout_ret=%.2f%% avg_dd=%.2f%%",
            result["verdict"],
            result["passes"],
            result["folds"],
            result["avg_holdout_return_pct"],
            result["avg_holdout_max_drawdown_pct"],
        )
        for fold in result["fold_results"]:
            if fold.get("fail_reasons"):
                for reason in fold["fail_reasons"]:
                    logger.warning("fold %s fail: %s", fold.get("fold"), reason)
    else:
        logger.info(
            "OOS validate train=%sd holdout=%sd tune=%s symbols=%s",
            args.train_days,
            args.holdout_days,
            not args.no_tune,
            symbols,
        )
        result = validator.run(**common)
        logger.info(
            "OOS verdict=%s train_ret=%.2f%% holdout_ret=%.2f%% holdout_dd=%.2f%%",
            result["verdict"],
            result["train"]["total_return_pct"],
            result["holdout"]["total_return_pct"],
            result["holdout"]["max_drawdown_pct"],
        )
        if result.get("fail_reasons"):
            for reason in result["fail_reasons"]:
                logger.warning("OOS fail: %s", reason)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Validação salva em %s", out)
    return 0 if result["passed"] else 1


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


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not args.yes:
        print("Confirme com --yes para liberar o circuit breaker.", file=sys.stderr)
        return 2
    memory = MemoryStore(config.memory.db_path)
    memory.resume_circuit()
    state = memory.get_robot_state()
    print(json.dumps({"resumed": True, "circuit_breaker": state}, indent=2, ensure_ascii=False))
    return 0


def cmd_halt(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if not args.yes:
        print("Confirme com --yes para pausar novas entradas manualmente.", file=sys.stderr)
        return 2
    memory = MemoryStore(config.memory.db_path)
    reason = args.reason or "manual_halt"
    memory.halt_circuit(reason)
    state = memory.get_robot_state()
    print(json.dumps({"halted": True, "circuit_breaker": state}, indent=2, ensure_ascii=False))
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

    run = sub.add_parser("run-once", help="Roda um ciclo de análise + execução")
    run.add_argument(
        "--confirm-testnet",
        action="store_true",
        help="Obrigatório quando mode=testnet",
    )
    run.add_argument(
        "--confirm-live",
        action="store_true",
        help="Obrigatório quando mode=live",
    )
    run.add_argument(
        "--confirm-live-orders",
        action="store_true",
        help="Obrigatório em mode=live com dry_run=false (ordens reais)",
    )
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
    loop.add_argument("--confirm-testnet", action="store_true")
    loop.add_argument("--confirm-live", action="store_true")
    loop.add_argument("--confirm-live-orders", action="store_true")
    loop.set_defaults(func=cmd_run_loop)

    live_check = sub.add_parser(
        "live-check",
        help="Valida API keys Binance (account) sem enviar ordens",
    )
    live_check.add_argument(
        "--mode",
        choices=["testnet", "live"],
        default=None,
        help="Força testnet ou live para o check (default: testnet se config=paper)",
    )
    live_check.set_defaults(func=cmd_live_check)

    status = sub.add_parser("status", help="Mostra caixa, reserva, posições e último ciclo")
    status.set_defaults(func=cmd_status)

    health = sub.add_parser(
        "health",
        help="Checa heartbeat do run-loop (vivo / stale / erro)",
    )
    health.set_defaults(func=cmd_health)

    dash = sub.add_parser(
        "dashboard",
        help="Sobe dashboard local de monitoramento (somente leitura)",
    )
    dash.add_argument("--host", default=None, help="Host bind (default: config)")
    dash.add_argument("--port", type=int, default=None, help="Porta (default: 8787)")
    dash.set_defaults(func=cmd_dashboard)

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

    resume = sub.add_parser("resume", help="Libera circuit breaker (permite novas compras)")
    resume.add_argument("--yes", action="store_true", help="Confirma o resume")
    resume.set_defaults(func=cmd_resume)

    halt = sub.add_parser("halt", help="Pausa novas compras manualmente")
    halt.add_argument("--yes", action="store_true", help="Confirma o halt")
    halt.add_argument("--reason", default="manual_halt", help="Motivo do halt")
    halt.set_defaults(func=cmd_halt)

    report = sub.add_parser("report", help="Gera relatório diário com alertas")
    report.add_argument(
        "--date",
        default=None,
        help="Dia UTC no formato YYYY-MM-DD (default: hoje)",
    )
    report.add_argument(
        "--text",
        action="store_true",
        help="Imprime resumo em texto em vez de JSON",
    )
    report.add_argument(
        "--full",
        action="store_true",
        help="Inclui decisions/fills/cycles no JSON",
    )
    report.set_defaults(func=cmd_report)

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

    validate = sub.add_parser(
        "validate",
        help="Backtest out-of-sample (treino + holdout)",
    )
    validate.add_argument("--train-days", type=int, default=60)
    validate.add_argument("--holdout-days", type=int, default=30)
    validate.add_argument("--symbols", default=None)
    validate.add_argument("--interval", default=None)
    validate.add_argument(
        "--no-tune",
        action="store_true",
        help="Usa parâmetros atuais sem retunar no treino",
    )
    validate.add_argument("--max-dd", type=float, default=8.0, help="Limite DD no tune")
    validate.add_argument(
        "--min-return",
        type=float,
        default=-2.0,
        help="Retorno mínimo aceitável no holdout (%)",
    )
    validate.add_argument(
        "--max-holdout-dd",
        type=float,
        default=6.0,
        help="Drawdown máximo aceitável no holdout (%)",
    )
    validate.add_argument(
        "--min-trades",
        type=int,
        default=2,
        help="Mínimo de trades no holdout",
    )
    validate.add_argument(
        "--max-drop",
        type=float,
        default=5.0,
        help="Queda máxima train→holdout em pontos percentuais",
    )
    validate.add_argument(
        "--walk-forward",
        action="store_true",
        help="Roda vários splits OOS deslocados (walk-forward)",
    )
    validate.add_argument(
        "--folds",
        type=int,
        default=3,
        help="Número de folds no walk-forward (default: 3)",
    )
    validate.add_argument("--save", default=None)
    validate.set_defaults(func=cmd_validate)

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
