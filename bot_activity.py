"""
One-shot snapshot: what the bot has been doing recently (DB + log tail).

Run from repo root:
    python bot_activity.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402


def main() -> None:
    os.chdir(ROOT)
    from core.runtime_paths import resolved_database_path

    db_path = resolved_database_path()
    print("=" * 72)
    print("BOT ACTIVITY SNAPSHOT", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("Database:", db_path)
    print("=" * 72)

    since_5m = int((datetime.utcnow() - timedelta(minutes=5)).timestamp())
    since_1h = int((datetime.utcnow() - timedelta(hours=1)).timestamp())

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) AS c FROM signals WHERE timestamp >= ?", (since_5m,)
        )
        print(f"\nSignals in last 5 minutes: {cur.fetchone()['c']}")
        cur.execute(
            "SELECT COUNT(*) AS c FROM signals WHERE timestamp >= ?", (since_1h,)
        )
        print(f"Signals in last 1 hour:    {cur.fetchone()['c']}")

        cur.execute(
            """
            SELECT agent_type, COUNT(*) AS c
            FROM signals WHERE timestamp >= ?
            GROUP BY agent_type ORDER BY c DESC
            """,
            (since_5m,),
        )
        rows = cur.fetchall()
        print("\nBy agent (5m):")
        if not rows:
            print("  (none)")
        else:
            for r in rows:
                print(f"  {r['agent_type']:<18} {r['c']}")

        cur.execute(
            "SELECT COUNT(*) AS c FROM candles WHERE timestamp >= ?", (since_5m,)
        )
        print(f"\nCandle rows written (5m): {cur.fetchone()['c']}")

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='aggregated_outcomes'"
        )
        if cur.fetchone():
            cur.execute(
                "SELECT COUNT(*) AS c FROM aggregated_outcomes WHERE signal_ts >= ? AND evaluated_at IS NULL",
                (since_5m,),
            )
            print(f"Outcome rows pending eval (recent): {cur.fetchone()['c']}")

        print("\n" + "-" * 72)
        print("Last 8 signals:")
        cur.execute(
            """
            SELECT datetime(timestamp, 'unixepoch') AS ts, agent_type, signal_type, symbol
            FROM signals ORDER BY timestamp DESC LIMIT 8
            """
        )
        for r in cur.fetchall():
            print(f"  {r['ts']}  {r['agent_type']}/{r['signal_type']}  {r['symbol'] or '-'}")
        conn.close()
    except Exception as e:
        print("\nDB error:", e)

    log_dir = ROOT / "logs"
    main_log = log_dir / "__main__.log"
    if main_log.exists():
        print("\n" + "-" * 72)
        print(f"Tail of {main_log.name} (last 12 lines):")
        try:
            lines = main_log.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-12:]:
                print(" ", line)
        except OSError as e:
            print("  (cannot read log)", e)
    else:
        print(f"\n(No file yet: {main_log})")

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
