"""Print agent-related DB activity (signals, candles, auxiliary tables)."""
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "crypto_analytics.db"


def check_database():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        print("=" * 70)
        print("AGENT / DATABASE ACTIVITY")
        print("=" * 70)

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"\nTables: {', '.join(tables)}")

        cursor.execute("SELECT COUNT(*) as total FROM signals")
        total = cursor.fetchone()["total"]
        print(f"\nTotal signals: {total}")

        cursor.execute(
            """
            SELECT agent_type, COUNT(*) as count
            FROM signals
            GROUP BY agent_type
            ORDER BY count DESC
        """
        )
        print("\nSignals by agent:")
        agent_stats = {}
        for row in cursor.fetchall():
            agent_stats[row["agent_type"]] = row["count"]
            print(f"  {row['agent_type']:20s} {row['count']:5d}")

        hour_ago = int((datetime.utcnow() - timedelta(hours=1)).timestamp())
        cursor.execute(
            """
            SELECT agent_type, COUNT(*) as count
            FROM signals
            WHERE timestamp > ?
            GROUP BY agent_type
            ORDER BY count DESC
        """,
            (hour_ago,),
        )
        recent = cursor.fetchall()
        print("\nLast hour:")
        if recent:
            for row in recent:
                print(f"  {row['agent_type']:20s} {row['count']:5d}")
        else:
            print("  (none)")

        print("\n" + "=" * 70)
        print("LAST 10 SIGNALS")
        print("=" * 70)
        cursor.execute(
            """
            SELECT agent_type, signal_type, symbol, priority, message, timestamp, sent_to_telegram
            FROM signals
            ORDER BY timestamp DESC
            LIMIT 10
        """
        )
        rows = cursor.fetchall()
        if not rows:
            print("\n(no rows)")
        else:
            for i, signal in enumerate(rows, 1):
                ts = datetime.fromtimestamp(signal["timestamp"])
                sent = "sent" if signal["sent_to_telegram"] else "pending"
                msg = (signal["message"] or "")[:80]
                print(f"\n{i}. [{sent}] {signal['agent_type']} {signal['signal_type']}")
                print(f"   symbol={signal['symbol']} priority={signal['priority']}")
                print(f"   time={ts} msg={msg}")

        print("\n" + "=" * 70)
        print("TOP SYMBOLS")
        print("=" * 70)
        cursor.execute(
            """
            SELECT symbol, COUNT(*) as count
            FROM signals
            WHERE symbol IS NOT NULL
            GROUP BY symbol
            ORDER BY count DESC
            LIMIT 10
        """
        )
        sym_rows = cursor.fetchall()
        if sym_rows:
            for row in sym_rows:
                print(f"  {row['symbol']:15s} {row['count']:5d}")
        else:
            print("  (none)")

        print("\n" + "=" * 70)
        print("CANDLES")
        print("=" * 70)
        cursor.execute("SELECT COUNT(*) as total FROM candles")
        candles_total = cursor.fetchone()["total"]
        print(f"Total candles: {candles_total}")

        for label, table in (
            ("whale_transactions", "whale_transactions"),
            ("liquidity_zones", "liquidity_zones"),
            ("anomalies", "anomalies"),
        ):
            if table in tables:
                cursor.execute(f"SELECT COUNT(*) as total FROM {table}")
                print(f"{label}: {cursor.fetchone()['total']}")

        five_min_ago = int((datetime.utcnow() - timedelta(minutes=5)).timestamp())
        cursor.execute(
            "SELECT COUNT(*) as count FROM signals WHERE timestamp > ?",
            (five_min_ago,),
        )
        recent_count = cursor.fetchone()["count"]
        print("\n" + "=" * 70)
        print("LAST 5 MINUTES")
        print("=" * 70)
        print(f"Signals: {recent_count}")

        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        if total > 0:
            print(f"OK — {total} rows, {len(agent_stats)} agent types, {candles_total} candles.")
        else:
            print("No signals yet — run main.py and wait for market events.")

        conn.close()
        print("=" * 70)

    except FileNotFoundError:
        print(f"Database {DB_PATH} not found. Run python main.py first.")
    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    check_database()
