"""Resolve safe end timestamps for historical research (avoid empty future windows)."""
from __future__ import annotations

import sqlite3
import time
from typing import Optional


def latest_data_timestamp(db_path: str) -> Optional[int]:
    """Latest timestamp present in signals or candles tables."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT MAX(ts) FROM (
                SELECT MAX(timestamp) AS ts FROM signals
                UNION ALL
                SELECT MAX(timestamp) AS ts FROM candles
            )
            """
        ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])
    finally:
        conn.close()


def resolve_research_end_ts(
    db_path: str,
    *,
    horizon_minutes: int = 30,
    explicit: Optional[int] = None,
) -> int:
    """
    Cap research end to matured data: min(now, latest_data - horizon).
    Prevents walk-forward windows that extend beyond stored candles/signals.
    """
    now = int(time.time())
    latest = latest_data_timestamp(db_path)
    if latest is None:
        cap = now
    else:
        mature = int(latest) - int(horizon_minutes) * 60
        cap = min(now, mature)
    if explicit is not None:
        return min(int(explicit), cap)
    return cap
