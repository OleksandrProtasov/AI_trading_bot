import sqlite3
import time
from datetime import datetime, timezone

from core.research_end_ts import latest_data_timestamp, resolve_research_end_ts
from core.runtime_paths import resolved_database_path

p = resolved_database_path()
c = sqlite3.connect(p)
now = int(time.time())
latest = latest_data_timestamp(p)
end = resolve_research_end_ts(p)

def fmt(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "n/a"

sig = c.execute(
    "SELECT COUNT(1), SUM(CASE WHEN agent_type='aggregator' THEN 1 ELSE 0 END) FROM signals"
).fetchone()
base = """
SELECT COUNT(1),
       AVG(CASE WHEN directional_hit=1 THEN 1.0 ELSE 0.0 END)*100,
       AVG(return_pct)
FROM aggregated_outcomes WHERE evaluated_at IS NOT NULL
"""
out = c.execute(base).fetchone()
buy = c.execute(base + " AND action='BUY'").fetchone()
sell = c.execute(base + " AND action='SELL'").fetchone()
b30 = c.execute(
    base + " AND action='BUY' AND signal_ts>=?",
    (now - 30 * 86400,),
).fetchone()
acts = c.execute(
    "SELECT action, COUNT(1) FROM aggregated_outcomes WHERE evaluated_at IS NOT NULL GROUP BY action"
).fetchall()
print("data_latest", fmt(latest), "lag_days", round((now - (latest or now)) / 86400, 1))
print("research_end", fmt(end))
print("signals_total", sig[0], "aggregator", sig[1])
print("outcomes_all", {"n": out[0], "hit_pct": out[1], "avg_ret": out[2]})
print("buy", {"n": buy[0], "hit_pct": buy[1], "avg_ret": buy[2]})
print("sell", {"n": sell[0], "hit_pct": sell[1], "avg_ret": sell[2]})
print("buy_30d", {"n": b30[0], "hit_pct": b30[1], "avg_ret": b30[2]})
print("by_action", acts)
