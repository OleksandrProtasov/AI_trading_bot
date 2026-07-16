"""Backfill 1m candles from Binance REST into SQLite (fills DB gaps when bot was offline)."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from core.runtime_paths import resolved_database_path

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
DEFAULT_INTERVAL = "1m"
LIMIT = 1000


def _fetch_klines(
    symbol: str,
    *,
    start_ms: int,
    end_ms: Optional[int],
    interval: str = DEFAULT_INTERVAL,
) -> List[list]:
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "startTime": start_ms,
        "limit": LIMIT,
    }
    if end_ms is not None:
        params["endTime"] = end_ms
    url = f"{BINANCE_KLINES}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "tradingBot-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _last_candle_ts(conn: sqlite3.Connection, symbol: str, timeframe: str) -> Optional[int]:
    row = conn.execute(
        """
        SELECT MAX(timestamp) FROM candles
        WHERE symbol = ? AND timeframe = ?
        """,
        (symbol.upper(), timeframe),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def backfill_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    start_ts: int,
    end_ts: int,
    timeframe: str = DEFAULT_INTERVAL,
    sleep_sec: float = 0.08,
) -> int:
    """Insert missing candles; returns rows written."""
    sym = symbol.upper()
    inserted = 0
    cursor_ms = max(0, int(start_ts) * 1000)
    end_ms = int(end_ts) * 1000

    while cursor_ms < end_ms:
        try:
            batch = _fetch_klines(sym, start_ms=cursor_ms, end_ms=end_ms, interval=timeframe)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(2.0)
                continue
            raise
        if not batch:
            break

        rows = []
        for k in batch:
            ts = int(k[0]) // 1000
            if ts > int(end_ts):
                continue
            rows.append(
                (
                    sym,
                    timeframe,
                    ts,
                    float(k[1]),
                    float(k[2]),
                    float(k[3]),
                    float(k[4]),
                    float(k[5]),
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO candles
            (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        inserted += len(rows)

        last_open_ms = int(batch[-1][0])
        next_ms = last_open_ms + 60_000
        if next_ms <= cursor_ms:
            break
        cursor_ms = next_ms
        time.sleep(sleep_sec)

    return inserted


def _default_symbols() -> List[str]:
    try:
        from config import config

        return list(config.default_symbols)
    except Exception:
        return [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "DOTUSDT",
            "AVAXUSDT",
        ]


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill Binance 1m candles into SQLite")
    p.add_argument("--days", type=int, default=90, help="How many days back from end-ts")
    p.add_argument("--end-ts", type=int, default=None, help="End timestamp (default: now)")
    p.add_argument("--symbols", type=str, default="", help="Comma-separated; default from config")
    p.add_argument("--timeframe", type=str, default=DEFAULT_INTERVAL)
    p.add_argument("--sleep-sec", type=float, default=0.08)
    p.add_argument("--db-path", type=str, default="")
    args = p.parse_args()

    db_path = args.db_path or resolved_database_path()
    end_ts = int(args.end_ts or time.time())
    start_ts = end_ts - int(args.days) * 86400
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = _default_symbols()

    conn = sqlite3.connect(db_path)
    total = 0
    print(f"[backfill] db={db_path} symbols={len(symbols)} range={start_ts}..{end_ts}")
    for sym in symbols:
        last = _last_candle_ts(conn, sym, args.timeframe)
        sym_start = start_ts if last is None else min(start_ts, last + 60)
        if sym_start >= end_ts:
            print(f"[backfill] {sym}: up to date (last={last})")
            continue
        n = backfill_symbol(
            conn,
            sym,
            start_ts=sym_start,
            end_ts=end_ts,
            timeframe=args.timeframe,
            sleep_sec=float(args.sleep_sec),
        )
        total += n
        last_after = _last_candle_ts(conn, sym, args.timeframe)
        ts_fmt = (
            datetime.fromtimestamp(last_after, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if last_after
            else "n/a"
        )
        print(f"[backfill] {sym}: +{n} rows, latest={ts_fmt} UTC")
    conn.close()
    print(f"[backfill] done, inserted={total}")


if __name__ == "__main__":
    main()
