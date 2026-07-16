"""Quick check: paper trades and skip reasons since timestamp."""
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime_paths import resolved_database_path

# Bot started ~2026-07-14 01:19 UTC
SINCE = int(datetime(2026, 7, 14, 1, 15, 0).timestamp())

p = resolved_database_path()
c = sqlite3.connect(p)

print("=== PAPER TRADES since bot start ===")
rows = c.execute(
    """
    SELECT id, symbol, action, entry, opened_at, status, exit_reason, pnl_pct
    FROM paper_trades WHERE opened_at >= ? ORDER BY opened_at DESC
    """,
    (SINCE,),
).fetchall()
print(f"count: {len(rows)}")
for r in rows[:15]:
    print(f"  #{r[0]} {r[1]} {r[2]} status={r[5]} pnl={r[7]} @ {datetime.utcfromtimestamp(r[4])}")

open_n = c.execute("SELECT COUNT(*) FROM paper_trades WHERE status='open'").fetchone()[0]
print(f"open positions total: {open_n}")

print("\n=== ALERT COUNTS ===")
try:
    for r in c.execute("SELECT day, kind, count FROM analyst_alert_counts ORDER BY day DESC LIMIT 10"):
        print(r)
except sqlite3.OperationalError as e:
    print(e)

print("\n=== TELEGRAM SENT (aggregator, since start) ===")
try:
    n = c.execute(
        """
        SELECT COUNT(*) FROM signals
        WHERE agent_type='aggregator' AND sent_to_telegram=1 AND timestamp >= ?
        """,
        (SINCE,),
    ).fetchone()[0]
    print("telegram sent:", n)
except Exception as e:
    print(e)

c.close()
