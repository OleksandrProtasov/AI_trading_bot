"""Outcome math and DB evaluation (no network)."""
import asyncio
import tempfile
from pathlib import Path

from core.outcome_math import compute_path_metrics


def test_compute_path_metrics_buy_up():
    candles = [
        {"low": 99.0, "high": 101.0, "close": 100.5},
        {"low": 100.0, "high": 102.0, "close": 101.5},
    ]
    m = compute_path_metrics(100.0, "BUY", candles, direction_threshold_pct=0.05)
    assert m is not None
    assert m["return_pct"] > 0.05
    assert m["directional_hit"] == 1
    assert m["max_adverse_pct"] < 0


def test_compute_path_metrics_sell_down():
    candles = [
        {"low": 99.0, "high": 100.5, "close": 99.5},
        {"low": 98.0, "high": 100.0, "close": 98.5},
    ]
    m = compute_path_metrics(100.0, "SELL", candles, direction_threshold_pct=0.05)
    assert m is not None
    assert m["return_pct"] < -0.05
    assert m["directional_hit"] == 1


def test_database_evaluate_aggregated_outcome():
    from core.database import Database

    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "t.db")
            db = Database(db_path)
            base_ts = 1_700_000_000
            for i in range(5):
                ts = base_ts + i * 60
                close = 100.0 + i * 0.5
                await db.save_candle(
                    "btcusdt",
                    "1m",
                    ts,
                    close - 0.2,
                    close + 0.2,
                    close - 0.3,
                    close,
                    1.0,
                )
            horizon = 4 * 60
            await db.insert_aggregated_outcome(
                signal_ts=base_ts,
                symbol="btcusdt",
                action="BUY",
                baseline_action="BUY",
                confidence=0.7,
                risk="Medium",
                price_at_signal=100.0,
                reasons=["test"],
                horizon_sec=horizon,
                council_enabled=True,
                council_changed=False,
                sent_telegram=False,
            )
            now_ts = base_ts + horizon + 10
            n = await db.evaluate_pending_aggregated_outcomes(
                now_ts, direction_threshold_pct=0.01
            )
            assert n == 1
            summary = await db.get_aggregated_outcomes_summary(base_ts - 1)
            assert summary.get("overall", {}).get("total_evaluated", 0) >= 1

    asyncio.run(_run())
