"""Resample 1m OHLC candles to higher timeframes."""
from __future__ import annotations

from typing import Any, Dict, List


def resample_ohlc(candles: List[Dict[str, Any]], bar_seconds: int) -> List[Dict[str, Any]]:
    if not candles or bar_seconds <= 0:
        return []
    buckets: Dict[int, Dict[str, Any]] = {}
    for c in sorted(candles, key=lambda x: int(x.get("timestamp", 0))):
        ts = int(c["timestamp"])
        bucket = (ts // bar_seconds) * bar_seconds
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        v = float(c.get("volume") or 0.0)
        if bucket not in buckets:
            buckets[bucket] = {
                "timestamp": bucket,
                "open": o,
                "high": h,
                "low": l,
                "close": cl,
                "volume": v,
            }
        else:
            b = buckets[bucket]
            b["high"] = max(float(b["high"]), h)
            b["low"] = min(float(b["low"]), l)
            b["close"] = cl
            b["volume"] = float(b["volume"]) + v
    return [buckets[k] for k in sorted(buckets)]


def load_candles_1m_sync(
    db_path: str,
    symbol: str,
    *,
    limit: int = 6000,
    end_ts: int | None = None,
) -> List[Dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(db_path, timeout=120)
    conn.row_factory = sqlite3.Row
    try:
        for sym in (symbol, symbol.upper(), symbol.lower()):
            if end_ts is not None:
                rows = conn.execute(
                    """
                    SELECT timestamp, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = ? AND timeframe = '1m' AND timestamp <= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (sym, int(end_ts), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT timestamp, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = ? AND timeframe = '1m'
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (sym, int(limit)),
                ).fetchall()
            if rows:
                out = [dict(r) for r in reversed(rows)]
                return out
        return []
    finally:
        conn.close()
