"""Inspect stored signals and Telegram delivery stats (read-only)."""
import sqlite3
from datetime import datetime

from config import config

conn = sqlite3.connect("crypto_analytics.db")
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 60)
print("SIGNAL DATABASE SUMMARY")
print("=" * 60)
print()

cursor.execute("SELECT COUNT(*) FROM signals")
total = cursor.fetchone()[0]
print(f"Total signals: {total}")

cursor.execute(
    """
    SELECT agent_type, COUNT(*) as count
    FROM signals
    GROUP BY agent_type
    ORDER BY count DESC
"""
)
print("\nBy agent:")
for row in cursor.fetchall():
    print(f"  {row['agent_type']}: {row['count']}")

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
print("\nTop symbols:")
for row in cursor.fetchall():
    is_stable = row["symbol"] in config.stable_coins or (
        row["symbol"].endswith("USDT") and len(row["symbol"]) == 4
    )
    tag = " (stable / filtered)" if is_stable else ""
    print(f"  {row['symbol']}: {row['count']}{tag}")

cursor.execute(
    """
    SELECT agent_type, signal_type, symbol, timestamp, sent_to_telegram
    FROM signals
    ORDER BY timestamp DESC
    LIMIT 10
"""
)
print("\nLast 10 signals:")
for row in cursor.fetchall():
    time_str = datetime.fromtimestamp(row["timestamp"]).strftime("%H:%M:%S")
    sent = "sent" if row["sent_to_telegram"] else "pending"
    print(
        f"  {time_str} | {row['agent_type']} | {row['signal_type']} | "
        f"{row['symbol']} | {sent}"
    )

cursor.execute("SELECT COUNT(*) FROM signals WHERE sent_to_telegram = 1")
sent = cursor.fetchone()[0]
pct = sent / total * 100 if total > 0 else 0
print(f"\nTelegram marked sent: {sent} / {total} ({pct:.1f}%)")

cursor.execute(
    """
    SELECT COUNT(*) FROM signals
    WHERE symbol IN ('USDT', 'USDC', 'BUSD')
    OR (symbol LIKE '%USDT' AND LENGTH(symbol) = 4)
"""
)
stable_count = cursor.fetchone()[0]
print(f"\nStable-coin-ish rows: {stable_count} (often filtered upstream)")

cursor.execute(
    """
    SELECT COUNT(*) FROM signals
    WHERE symbol IS NOT NULL
    AND symbol NOT IN ('USDT', 'USDC', 'BUSD')
    AND LENGTH(symbol) >= 6
"""
)
valid_count = cursor.fetchone()[0]
print(f"Non-trivial pairs (len>=6): {valid_count}")

conn.close()

print("\n" + "=" * 60)
print("NOTES")
print("=" * 60)
if stable_count > total * 0.8:
    print("Most rows look like stable filters — normal if feeds include USDT legs.")
elif sent == 0 and valid_count > 0:
    print("Valid rows exist but nothing flagged sent — check aggregator thresholds / Telegram.")
else:
    print("Pipeline looks healthy: rows are being persisted.")
