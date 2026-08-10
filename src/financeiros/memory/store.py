from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from financeiros.capital.portfolio import Portfolio
from financeiros.models import Decision, OrderFill, Position, Side, utc_now


class MemoryStore:
    """SQLite local: decisões, fills, lições, portfólio e ciclos."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    rationale TEXT NOT NULL,
                    signal_strength REAL NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    notional REAL NOT NULL,
                    tags TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filled_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL,
                    notional REAL NOT NULL,
                    paper INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT,
                    lesson TEXT NOT NULL,
                    source_decision_id INTEGER,
                    tags TEXT NOT NULL
                );

                -- Estado atual do portfólio paper (uma linha)
                CREATE TABLE IF NOT EXISTS portfolio_cash (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash_usdt REAL NOT NULL,
                    reserve_usdt REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL,
                    avg_price REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    cash_usdt REAL NOT NULL,
                    equity_usdt REAL NOT NULL,
                    decisions_count INTEGER NOT NULL,
                    approved_count INTEGER NOT NULL,
                    summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reserve_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT,
                    amount REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    skim_pct REAL NOT NULL,
                    note TEXT NOT NULL
                );
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_cash)").fetchall()}
        if cols and "reserve_usdt" not in cols:
            conn.execute(
                "ALTER TABLE portfolio_cash ADD COLUMN reserve_usdt REAL NOT NULL DEFAULT 0"
            )

    def record_decision(self, decision: Decision) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO decisions (
                    created_at, symbol, side, approved, rationale, signal_strength,
                    price, quantity, notional, tags, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.created_at.isoformat(),
                    decision.symbol,
                    decision.side.value,
                    int(decision.approved),
                    decision.rationale,
                    decision.signal_strength,
                    decision.price,
                    decision.quantity,
                    decision.notional,
                    json.dumps(decision.tags),
                    json.dumps(decision.metadata),
                ),
            )
            return int(cur.lastrowid)

    def record_fill(self, fill: OrderFill) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO fills (
                    filled_at, symbol, side, quantity, price, fee, notional, paper
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.filled_at.isoformat(),
                    fill.symbol,
                    fill.side.value,
                    fill.quantity,
                    fill.price,
                    fill.fee,
                    fill.notional,
                    int(fill.paper),
                ),
            )
            return int(cur.lastrowid)

    def add_lesson(
        self,
        lesson: str,
        symbol: str | None = None,
        source_decision_id: int | None = None,
        tags: list[str] | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO lessons (created_at, symbol, lesson, source_decision_id, tags)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    utc_now().isoformat(),
                    symbol,
                    lesson,
                    source_decision_id,
                    json.dumps(tags or []),
                ),
            )
            return int(cur.lastrowid)

    def recent_decisions(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        query = "SELECT * FROM decisions"
        params: list[object] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def similar_rejected_patterns(self, symbol: str, side: Side, limit: int = 5) -> list[str]:
        """Busca rejeições recentes de BUY/SELL para evitar repetir erro óbvio.

        HOLD não entra: é estado neutro e poluiria a memória a cada ciclo.
        """
        if side == Side.HOLD:
            return []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT rationale FROM decisions
                WHERE symbol = ? AND side = ? AND approved = 0
                  AND side != 'hold'
                ORDER BY id DESC LIMIT ?
                """,
                (symbol, side.value, limit),
            ).fetchall()
        # Dedup preservando ordem
        seen: set[str] = set()
        lessons: list[str] = []
        for row in rows:
            text = row["rationale"]
            # Evita eco recursivo de blocos "Memória recente:"
            if "Memória recente:" in text:
                text = text.split("Memória recente:")[0].strip()
            if text and text not in seen:
                seen.add(text)
                lessons.append(text)

        with self._connect() as conn:
            lesson_rows = conn.execute(
                """
                SELECT lesson FROM lessons
                WHERE symbol = ? OR symbol IS NULL
                ORDER BY id DESC LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        for row in lesson_rows:
            text = row["lesson"]
            if text and text not in seen:
                seen.add(text)
                lessons.append(text)
        return lessons

    def load_portfolio(self, starting_cash_usdt: float) -> Portfolio:
        """Carrega portfólio persistido ou inicializa com caixa inicial."""
        with self._connect() as conn:
            cash_row = conn.execute(
                "SELECT cash_usdt, reserve_usdt FROM portfolio_cash WHERE id = 1"
            ).fetchone()
            if cash_row is None:
                now = utc_now().isoformat()
                conn.execute(
                    """
                    INSERT INTO portfolio_cash (id, cash_usdt, reserve_usdt, updated_at)
                    VALUES (1, ?, 0, ?)
                    """,
                    (float(starting_cash_usdt), now),
                )
                return Portfolio(starting_cash_usdt)

            portfolio = Portfolio(
                float(cash_row["cash_usdt"]),
                reserve_usdt=float(cash_row["reserve_usdt"] or 0.0),
            )
            pos_rows = conn.execute(
                "SELECT symbol, quantity, avg_price FROM positions WHERE quantity > 0"
            ).fetchall()
            for row in pos_rows:
                portfolio.positions[row["symbol"]] = Position(
                    symbol=row["symbol"],
                    quantity=float(row["quantity"]),
                    avg_price=float(row["avg_price"]),
                )
            return portfolio

    def save_portfolio(self, portfolio: Portfolio) -> None:
        """Persiste caixa, reserva e posições de forma atômica."""
        now = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_cash (id, cash_usdt, reserve_usdt, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    cash_usdt = excluded.cash_usdt,
                    reserve_usdt = excluded.reserve_usdt,
                    updated_at = excluded.updated_at
                """,
                (float(portfolio.cash_usdt), float(portfolio.reserve_usdt), now),
            )
            conn.execute("DELETE FROM positions")
            for symbol, pos in portfolio.positions.items():
                if pos.quantity <= 0:
                    continue
                conn.execute(
                    """
                    INSERT INTO positions (symbol, quantity, avg_price, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (symbol, float(pos.quantity), float(pos.avg_price), now),
                )

    def record_reserve_transfer(
        self,
        *,
        amount: float,
        realized_pnl: float,
        skim_pct: float,
        symbol: str | None = None,
        note: str = "",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reserve_transfers (
                    created_at, symbol, amount, realized_pnl, skim_pct, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now().isoformat(),
                    symbol,
                    float(amount),
                    float(realized_pnl),
                    float(skim_pct),
                    note,
                ),
            )
            return int(cur.lastrowid)

    def recent_reserve_transfers(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, symbol, amount, realized_pnl, skim_pct, note
                FROM reserve_transfers
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_cycle(
        self,
        *,
        started_at: str,
        finished_at: str,
        cash_usdt: float,
        equity_usdt: float,
        decisions: list[Decision],
        reserve_usdt: float = 0.0,
        total_wealth_usdt: float | None = None,
    ) -> int:
        approved = sum(1 for d in decisions if d.approved)
        wealth = total_wealth_usdt if total_wealth_usdt is not None else equity_usdt + reserve_usdt
        summary = {
            "symbols": [d.symbol for d in decisions],
            "sides": [d.side.value for d in decisions],
            "approved": [d.symbol for d in decisions if d.approved],
            "reserve_usdt": reserve_usdt,
            "total_wealth_usdt": wealth,
        }
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO cycles (
                    started_at, finished_at, cash_usdt, equity_usdt,
                    decisions_count, approved_count, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    started_at,
                    finished_at,
                    cash_usdt,
                    equity_usdt,
                    len(decisions),
                    approved,
                    json.dumps(summary),
                ),
            )
            return int(cur.lastrowid)

    def get_status(self) -> dict:
        with self._connect() as conn:
            cash_row = conn.execute(
                "SELECT cash_usdt, reserve_usdt, updated_at FROM portfolio_cash WHERE id = 1"
            ).fetchone()
            positions = [
                dict(r)
                for r in conn.execute(
                    "SELECT symbol, quantity, avg_price, updated_at FROM positions ORDER BY symbol"
                ).fetchall()
            ]
            last_cycle = conn.execute(
                """
                SELECT id, started_at, finished_at, cash_usdt, equity_usdt,
                       decisions_count, approved_count, summary_json
                FROM cycles ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            decision_count = conn.execute("SELECT COUNT(*) AS c FROM decisions").fetchone()["c"]
            fill_count = conn.execute("SELECT COUNT(*) AS c FROM fills").fetchone()["c"]
            reserve_total = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM reserve_transfers"
            ).fetchone()["s"]

        cash = float(cash_row["cash_usdt"]) if cash_row else None
        reserve = float(cash_row["reserve_usdt"] or 0.0) if cash_row else 0.0
        return {
            "cash_usdt": cash,
            "reserve_usdt": reserve,
            "portfolio_updated_at": cash_row["updated_at"] if cash_row else None,
            "positions": positions,
            "decision_count": decision_count,
            "fill_count": fill_count,
            "reserve_skimmed_total": float(reserve_total),
            "last_cycle": dict(last_cycle) if last_cycle else None,
        }

    def recent_cycles(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, started_at, finished_at, cash_usdt, equity_usdt,
                       decisions_count, approved_count, summary_json
                FROM cycles ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def reset_portfolio(
        self,
        starting_cash_usdt: float,
        *,
        keep_reserve: bool = False,
    ) -> None:
        """Zera posições e redefine caixa. Mantém histórico de decisões/ciclos."""
        now = utc_now().isoformat()
        with self._connect() as conn:
            reserve = 0.0
            if keep_reserve:
                row = conn.execute(
                    "SELECT reserve_usdt FROM portfolio_cash WHERE id = 1"
                ).fetchone()
                reserve = float(row["reserve_usdt"] or 0.0) if row else 0.0
            conn.execute("DELETE FROM positions")
            conn.execute(
                """
                INSERT INTO portfolio_cash (id, cash_usdt, reserve_usdt, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    cash_usdt = excluded.cash_usdt,
                    reserve_usdt = excluded.reserve_usdt,
                    updated_at = excluded.updated_at
                """,
                (float(starting_cash_usdt), reserve, now),
            )
