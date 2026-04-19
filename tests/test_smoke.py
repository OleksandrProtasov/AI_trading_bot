"""Import and lightweight behavior checks (no network, no Telegram)."""
import asyncio
import tempfile
from pathlib import Path

def test_import_core():
    from core.database import Database
    from core.event_router import EventRouter, Signal, Priority
    from core.health_check import HealthCheck
    from core.metrics import Metrics
    from core.utils import is_stable_coin

    assert Priority.HIGH.value == "high"
    assert is_stable_coin("USDT", {"USDT"})
    assert not is_stable_coin("BTCUSDT", {"USDT"})


def test_database_signal_roundtrip():
    from core.database import Database
    from core.event_router import Signal, Priority

    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "test.db")
            db = Database(db_path)
            sig = Signal(
                agent_type="market",
                signal_type="volume_spike",
                priority=Priority.MEDIUM,
                message="test message",
                symbol="BTCUSDT",
                data={"price": 1.0},
            )
            sid = await db.save_signal(
                agent_type=sig.agent_type,
                signal_type=sig.signal_type,
                priority=sig.priority.value,
                message=sig.message,
                symbol=sig.symbol,
                data=sig.data,
            )
            assert sid is not None
            if sid:
                await db.mark_signal_sent(sid)

    asyncio.run(_run())


def test_config_example_loads():
    example = Path(__file__).resolve().parent.parent / "config.py.example"
    assert example.is_file()
    ns: dict = {}
    exec(example.read_text(encoding="utf-8"), ns)
    assert "TELEGRAM_BOT_TOKEN" in ns or "config" in ns


def test_aggregator_reasons_english_branch():
    from agents.aggregator_agent import AggregatorAgent
    from core.database import Database
    from core.event_router import Signal, Priority

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "agg.db"))
        agg = AggregatorAgent(db, None, None)
        s = Signal(
            agent_type="market",
            signal_type="volume_spike",
            priority=Priority.HIGH,
            message="Volume spike detected",
            symbol="BTCUSDT",
            data={"volume_spike": 2.5},
        )
        reasons = agg._extract_reasons([s])
        assert reasons
        assert all(isinstance(r, str) for r in reasons)
