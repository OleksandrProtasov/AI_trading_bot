"""Build reports/agent_weights.json from evaluated outcomes."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from core.agent_weights import build_agent_weights, save_agent_weights
from core.research_end_ts import resolve_research_end_ts
from core.runtime_paths import resolved_database_path


def main() -> None:
    p = argparse.ArgumentParser(description="Sync agent weight multipliers from DB edge")
    p.add_argument("--hours", type=int, default=24 * 30)
    p.add_argument("--lookback-sec", type=int, default=180)
    args = p.parse_args()

    db_path = resolved_database_path()
    end_ts = resolve_research_end_ts(db_path)
    since_ts = max(0, int(end_ts) - int(args.hours) * 3600)
    payload = build_agent_weights(
        db_path,
        since_ts=since_ts,
        lookback_sec=int(args.lookback_sec),
    )
    if int(payload.get("edge_outcomes") or 0) == 0 and since_ts > 0:
        payload = build_agent_weights(
            db_path,
            since_ts=0,
            lookback_sec=int(args.lookback_sec),
        )
    path = save_agent_weights(payload)
    print(json.dumps({"saved": str(path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
