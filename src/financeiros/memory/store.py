from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from financeiros.models import Decision, OrderFill, Side, utc_now


class MemoryStore:
    """SQLite local: decisões, fills e lições aprendidas."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
                """
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
        """Busca rejeições recentes no mesmo símbolo/lado para evitar repetir erro óbvio."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT rationale, tags FROM decisions
                WHERE symbol = ? AND side = ? AND approved = 0
                ORDER BY id DESC LIMIT ?
                """,
                (symbol, side.value, limit),
            ).fetchall()
        lessons = [r["rationale"] for r in rows]
        with self._connect() as conn:
            lesson_rows = conn.execute(
                """
                SELECT lesson FROM lessons
                WHERE symbol = ? OR symbol IS NULL
                ORDER BY id DESC LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        lessons.extend(r["lesson"] for r in lesson_rows)
        return lessons
