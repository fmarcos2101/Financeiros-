from pathlib import Path

from financeiros.memory.store import MemoryStore
from financeiros.models import Decision, Side


def test_memory_records_and_recalls(tmp_path: Path):
    store = MemoryStore(tmp_path / "test.db")
    decision = Decision(
        symbol="BTCUSDT",
        side=Side.BUY,
        approved=False,
        rationale="Volatilidade 0.09 acima do limite 0.08.",
        signal_strength=0.4,
        price=60000,
        quantity=0,
        notional=0,
        tags=["paper"],
    )
    decision_id = store.record_decision(decision)
    assert decision_id > 0

    store.add_lesson("Não comprar em funding extremo", symbol="BTCUSDT")
    hints = store.similar_rejected_patterns("BTCUSDT", Side.BUY)
    assert any("Volatilidade" in h for h in hints)
    assert any("funding" in h for h in hints)

    rows = store.recent_decisions(symbol="BTCUSDT", limit=5)
    assert len(rows) == 1
