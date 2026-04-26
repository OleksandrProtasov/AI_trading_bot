import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

from core.backtest_portfolio import (
    BacktestConfig,
    infer_side_from_raw_signal,
    run_aggregator_backtest,
    run_backtest_compare,
    run_raw_signals_backtest,
)
from core.database import Database


def _insert_signal(
    db_path: str,
    ts: int,
    agent_type: str,
    signal_type: str,
    symbol: str,
    priority: str,
    message: str,
    data: dict,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO signals (timestamp, agent_type, symbol, signal_type, priority, message, data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            agent_type,
            symbol,
            signal_type,
            priority,
            message,
            json.dumps(data) if data else None,
        ),
    )
    conn.commit()
    conn.close()


def test_backtest_aggregator_signal_buy_positive():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "b.db")
        db = Database(db_path)
        base = 1_700_000_000

        async def _seed():
            for i in range(0, 360):
                ts = base + i * 60
                close = 100.0 + i * 0.02
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        sig_ts = base + 180 * 60
        _insert_signal(
            db_path,
            sig_ts,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "BUY signal for BTCUSDT",
            {"action": "BUY", "confidence": 0.8, "price": 100.0},
        )
        res = run_aggregator_backtest(
            db_path,
            start_ts=base,
            end_ts=base + 400 * 60,
            cfg=BacktestConfig(min_confidence=0.55, horizon_minutes=60, fee_bps_per_side=0),
        )
        assert res["trades"] == 1
        assert res["total_return_pct"] > 0


def test_infer_side_keywords():
    assert infer_side_from_raw_signal("volume_spike") == "BUY"
    assert infer_side_from_raw_signal("rapid_dump") == "SELL"
    assert infer_side_from_raw_signal("support_break") == "SELL"


def test_raw_signals_backtest_rising_market():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "r.db")
        db = Database(db_path)
        base = 1_800_000_000

        async def _seed():
            for i in range(0, 200):
                ts = base + i * 60
                close = 50.0 + i * 0.01
                await db.save_candle(
                    "ETHUSDT", "1m", ts, close - 0.05, close + 0.05, close - 0.08, close, 1.0
                )

        asyncio.run(_seed())
        sig_ts = base + 30 * 60
        _insert_signal(
            db_path,
            sig_ts,
            "market",
            "volume_spike",
            "ETHUSDT",
            "high",
            "spike",
            {"price": 50.0},
        )
        end_ts = base + 199 * 60
        res = run_raw_signals_backtest(
            db_path,
            start_ts=base,
            end_ts=end_ts,
            cfg=BacktestConfig(
                min_confidence=0.5,
                horizon_minutes=60,
                fee_bps_per_side=0,
                max_open_positions=1,
            ),
            agent_types=("market",),
        )
        assert res["trades"] >= 1
        assert res["total_return_pct"] > 0


def test_compare_runs_without_crash():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "c.db")
        db = Database(db_path)
        base = 1_900_000_000

        async def _seed():
            for i in range(0, 120):
                ts = base + i * 60
                close = 100.0 + i * 0.01
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        sig_ts = base + 10 * 60
        _insert_signal(
            db_path,
            sig_ts,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "x",
            {"action": "BUY", "confidence": 0.9, "price": 100.0},
        )
        end_ts = base + 119 * 60
        out = run_backtest_compare(
            db_path,
            start_ts=base,
            end_ts=end_ts,
            cfg=BacktestConfig(
                min_confidence=0.55,
                horizon_minutes=1,
                fee_bps_per_side=0,
                max_open_positions=1,
            ),
            raw_agent_types=("market",),
        )
        assert "aggregator" in out and "raw" in out and "note" in out


def test_only_matured_signals_filters_fresh_entries():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "m.db")
        db = Database(db_path)
        base = 2_000_000_000

        async def _seed():
            for i in range(0, 120):
                ts = base + i * 60
                close = 200.0 + i * 0.02
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        # Signal near the end of data - mature only if include_unmatured / explicit end provided.
        _insert_signal(
            db_path,
            base + 118 * 60,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "late",
            {"action": "BUY", "confidence": 0.9, "price": 202.0},
        )
        matured = run_aggregator_backtest(
            db_path,
            start_ts=base,
            cfg=BacktestConfig(
                min_confidence=0.4,
                horizon_minutes=10,
                fee_bps_per_side=0,
                only_matured_signals=True,
            ),
        )
        # With auto "matured only", synthetic future timestamps may fall outside now-window.
        if "error" in matured:
            assert "No aggregator signals" in matured["error"]
        else:
            assert matured["trades"] == 0

        unmatured = run_aggregator_backtest(
            db_path,
            start_ts=base,
            end_ts=base + 119 * 60,
            cfg=BacktestConfig(
                min_confidence=0.4,
                horizon_minutes=10,
                fee_bps_per_side=0,
                only_matured_signals=False,
            ),
        )
        # still may be 0 due to no future candle, but path is exercised and no crash
        assert "trades" in unmatured and "skipped" in unmatured


def test_defensive_mode_filters_more_than_balanced():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "s.db")
        db = Database(db_path)
        base = 2_100_000_000

        async def _seed():
            for i in range(0, 120):
                ts = base + i * 60
                close = 100.0 + i * 0.03
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.2, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        _insert_signal(
            db_path,
            base + 30 * 60,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "buy weak",
            {"action": "BUY", "confidence": 0.56, "price": 101.0, "reasons": ["Volume spike"]},
        )
        common = dict(
            start_ts=base,
            end_ts=base + 110 * 60,
            cfg=BacktestConfig(
                min_confidence=0.4,
                strategy_min_confidence=0.55,
                horizon_minutes=15,
                fee_bps_per_side=0,
            ),
        )
        b_cfg = common["cfg"]
        b_cfg.strategy_mode = "balanced"
        balanced = run_aggregator_backtest(db_path, start_ts=common["start_ts"], end_ts=common["end_ts"], cfg=b_cfg)

        d_cfg = BacktestConfig(
            min_confidence=0.4,
            strategy_min_confidence=0.55,
            horizon_minutes=15,
            fee_bps_per_side=0,
            strategy_mode="defensive",
        )
        defensive = run_aggregator_backtest(db_path, start_ts=common["start_ts"], end_ts=common["end_ts"], cfg=d_cfg)
        assert defensive["trades"] <= balanced["trades"]


def test_allocator_limits_reduce_trade_count():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "alloc.db")
        db = Database(db_path)
        base = 2_200_000_000

        async def _seed():
            for i in range(0, 200):
                ts = base + i * 60
                close = 100.0 + i * 0.01
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        for i in range(5):
            _insert_signal(
                db_path,
                base + (10 + i * 20) * 60,
                "aggregator",
                "buy",
                "BTCUSDT",
                "high",
                f"s{i}",
                {"action": "BUY", "confidence": 0.9, "price": 100.0, "reasons": ["Level break"]},
            )

        unconstrained = run_aggregator_backtest(
            db_path,
            start_ts=base,
            end_ts=base + 199 * 60,
            cfg=BacktestConfig(
                min_confidence=0.4,
                horizon_minutes=10,
                fee_bps_per_side=0,
                strategy_mode="balanced",
            ),
        )
        constrained = run_aggregator_backtest(
            db_path,
            start_ts=base,
            end_ts=base + 199 * 60,
            cfg=BacktestConfig(
                min_confidence=0.4,
                horizon_minutes=10,
                fee_bps_per_side=0,
                strategy_mode="balanced",
                min_gap_between_entries_sec=3600,
                per_symbol_cooldown_sec=3600,
                max_trades_per_symbol=1,
            ),
        )
        assert constrained["trades"] <= unconstrained["trades"]
        assert constrained["skipped"]["allocator_filtered"] >= 1


def test_bearish_guard_blocks_buy_with_dump_reasons():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bg.db")
        db = Database(db_path)
        base = 2_300_000_000

        async def _seed():
            for i in range(0, 120):
                ts = base + i * 60
                close = 100.0 + i * 0.01
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        _insert_signal(
            db_path,
            base + 40 * 60,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "guard test",
            {
                "action": "BUY",
                "confidence": 0.9,
                "price": 100.0,
                "reasons": ["Dump risk", "Exit pressure cluster"],
            },
        )
        cfg = BacktestConfig(
            min_confidence=0.4,
            horizon_minutes=15,
            fee_bps_per_side=0,
            strategy_mode="defensive",
            strategy_bearish_guard_enabled=True,
            strategy_bearish_guard_threshold=1,
        )
        out = run_aggregator_backtest(
            db_path, start_ts=base, end_ts=base + 110 * 60, cfg=cfg
        )
        assert out["trades"] == 0
        assert out["skipped"]["strategy_filtered"] >= 1


def test_emergency_quota_limits_raw_trades():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "quota.db")
        db = Database(db_path)
        base = 2_400_000_000

        async def _seed():
            for i in range(0, 200):
                ts = base + i * 60
                close = 100.0 + i * 0.01
                await db.save_candle(
                    "ETHUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        for i in range(4):
            _insert_signal(
                db_path,
                base + (5 + i * 5) * 60,
                "emergency",
                "volume_spike",
                "ETHUSDT",
                "urgent",
                "e",
                {"price": 100.0},
            )
        out = run_raw_signals_backtest(
            db_path,
            start_ts=base,
            end_ts=base + 180 * 60,
            cfg=BacktestConfig(
                min_confidence=0.4,
                strategy_min_confidence=0.4,
                strategy_mode="balanced",
                horizon_minutes=5,
                fee_bps_per_side=0,
                emergency_trade_quota_per_hour=1,
            ),
            agent_types=("emergency",),
        )
        # Depending on timestamp bucket alignment, first two entries can land in adjacent hours.
        assert out["trades"] <= 2
        assert out["skipped"]["emergency_quota_filtered"] >= 1


def test_cooloff_override_allows_exceptional_entry():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "cooloff_override.db")
        db = Database(db_path)
        base = 2_500_000_000

        async def _seed():
            # Downtrend to force first two losses for BUY signals.
            for i in range(0, 220):
                ts = base + i * 60
                close = 200.0 - i * 0.05
                await db.save_candle(
                    "BTCUSDT", "1m", ts, close - 0.1, close + 0.1, close - 0.2, close, 1.0
                )

        asyncio.run(_seed())
        # First two are likely losers -> triggers cooloff.
        _insert_signal(
            db_path,
            base + 30 * 60,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "n1",
            {"action": "BUY", "confidence": 0.7, "price": 199.0, "source_signals_count": 2},
        )
        _insert_signal(
            db_path,
            base + 50 * 60,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "n2",
            {"action": "BUY", "confidence": 0.72, "price": 198.0, "source_signals_count": 2},
        )
        # This one lands during cooloff but must pass due to exceptional quality.
        _insert_signal(
            db_path,
            base + 55 * 60,
            "aggregator",
            "buy",
            "BTCUSDT",
            "high",
            "exceptional",
            {"action": "BUY", "confidence": 0.95, "price": 197.8, "source_signals_count": 4},
        )

        out = run_aggregator_backtest(
            db_path,
            start_ts=base,
            end_ts=base + 210 * 60,
            cfg=BacktestConfig(
                min_confidence=0.4,
                horizon_minutes=10,
                fee_bps_per_side=0,
                strategy_mode="balanced",
                loss_streak_for_cooloff=2,
                cooloff_skipped_entries=2,
                cooloff_override_confidence=0.9,
                cooloff_override_confirmations=3,
            ),
        )
        assert out["trades"] >= 2
        assert out["skipped"]["cooloff_override_passed"] >= 1
