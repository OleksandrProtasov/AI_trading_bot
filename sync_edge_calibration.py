"""Build edge gate calibration from evaluated outcomes."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from core.edge_calibration import build_edge_calibration, save_calibration
from core.research_end_ts import resolve_research_end_ts
from core.runtime_paths import resolved_database_path


def main() -> None:
    p = argparse.ArgumentParser(description="Sync outcome-based edge calibration")
    p.add_argument("--hours", type=int, default=24 * 30)
    p.add_argument("--min-samples", type=int, default=30)
    p.add_argument("--net-target-pct", type=float, default=0.08)
    p.add_argument("--min-hit-rate", type=float, default=0.45)
    p.add_argument("--weak-return-pct", type=float, default=0.05)
    p.add_argument("--max-extra-bps", type=float, default=30.0)
    args = p.parse_args()

    db_path = resolved_database_path()
    end_ts = resolve_research_end_ts(db_path)
    since_ts = int(end_ts) - int(args.hours) * 3600
    try:
        from config import config as app_config

        min_hit = float(app_config.agent.agg_edge_calibration_min_hit_rate)
        weak_ret = float(app_config.agent.agg_edge_calibration_weak_return_pct)
    except Exception:
        min_hit = float(args.min_hit_rate)
        weak_ret = float(args.weak_return_pct)
    payload = build_edge_calibration(
        resolved_database_path(),
        since_ts=since_ts,
        min_samples=int(args.min_samples),
        net_target_pct=float(args.net_target_pct),
        min_hit_rate=min_hit,
        weak_return_pct=weak_ret,
        max_extra_bps=float(args.max_extra_bps),
    )
    path = save_calibration(payload)
    print(json.dumps({"saved": str(path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
