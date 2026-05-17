"""CLI: which agents contribute positive/negative edge on evaluated outcomes."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from core.agent_edge import compute_agent_edge
from core.runtime_paths import resolved_database_path


def main() -> None:
    p = argparse.ArgumentParser(description="Agent edge report from outcomes + raw signals")
    p.add_argument("--hours", type=int, default=24 * 30)
    p.add_argument("--lookback-sec", type=int, default=180)
    args = p.parse_args()

    since_ts = int((datetime.utcnow() - timedelta(hours=args.hours)).timestamp())
    report = compute_agent_edge(
        resolved_database_path(),
        since_ts=since_ts,
        lookback_sec=int(args.lookback_sec),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
