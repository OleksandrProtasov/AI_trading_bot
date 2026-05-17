"""Attribute evaluated outcomes to contributing raw signal agents."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Dict, List


def compute_agent_edge(
    db_path: str,
    *,
    since_ts: int,
    lookback_sec: int = 180,
) -> Dict[str, Any]:
    """
    For each evaluated outcome, find raw signals in [signal_ts-lookback, signal_ts]
    and attribute return/hit to contributing agent_type buckets.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, signal_ts, symbol, action, return_pct, directional_hit
            FROM aggregated_outcomes
            WHERE evaluated_at IS NOT NULL
              AND signal_ts >= ?
              AND directional_hit IS NOT NULL
            """,
            (since_ts,),
        )
        outcomes = cur.fetchall()
        if not outcomes:
            return {"since_ts": since_ts, "outcomes": 0, "by_agent": []}

        stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"n": 0.0, "hits": 0.0, "ret_sum": 0.0}
        )
        for row in outcomes:
            sym = row["symbol"]
            ts = int(row["signal_ts"])
            cur.execute(
                """
                SELECT DISTINCT agent_type
                FROM signals
                WHERE symbol = ?
                  AND timestamp BETWEEN ? AND ?
                  AND agent_type != 'aggregator'
                """,
                (sym, ts - lookback_sec, ts),
            )
            agents = [r[0].lower() for r in cur.fetchall() if r[0]]
            if not agents:
                agents = ["unknown"]
            ret = float(row["return_pct"] or 0.0)
            hit = float(row["directional_hit"] or 0)
            for agent in agents:
                bucket = stats[agent]
                bucket["n"] += 1.0
                bucket["hits"] += hit
                bucket["ret_sum"] += ret

        rows: List[Dict[str, Any]] = []
        for agent, b in stats.items():
            n = int(b["n"])
            if n <= 0:
                continue
            rows.append(
                {
                    "agent_type": agent,
                    "attributions": n,
                    "hit_rate": b["hits"] / n,
                    "avg_return_pct": b["ret_sum"] / n,
                }
            )
        rows.sort(key=lambda x: x["avg_return_pct"], reverse=True)
        return {
            "since_ts": since_ts,
            "outcomes": len(outcomes),
            "lookback_sec": lookback_sec,
            "by_agent": rows,
        }
    finally:
        conn.close()
