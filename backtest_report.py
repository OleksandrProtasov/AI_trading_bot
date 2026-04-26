"""Run historical backtest over stored aggregator signals."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from core.backtest_portfolio import (
    BacktestConfig,
    DEFAULT_RAW_AGENT_TYPES,
    run_aggregator_backtest,
    run_backtest_compare,
    run_raw_signals_backtest,
)
from core.runtime_paths import resolved_database_path


def _ts_hours_ago(hours: int) -> int:
    return int((datetime.utcnow() - timedelta(hours=hours)).timestamp())


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest on historical DB signals")
    p.add_argument(
        "--mode",
        choices=("compare", "aggregator", "raw"),
        default="compare",
        help="compare = aggregator vs raw agents; aggregator/raw = single run",
    )
    p.add_argument("--hours", type=int, default=24 * 30)
    p.add_argument("--min-confidence", type=float, default=0.55)
    p.add_argument("--horizon-minutes", type=int, default=240)
    p.add_argument("--fee-bps", type=float, default=5.0)
    p.add_argument("--max-open", type=int, default=1)
    p.add_argument(
        "--include-unmatured",
        action="store_true",
        help="Include fresh signals that may not have full horizon yet",
    )
    p.add_argument(
        "--raw-agents",
        type=str,
        default=",".join(DEFAULT_RAW_AGENT_TYPES),
        help="Comma list, e.g. market,liquidity,onchain,emergency",
    )
    p.add_argument(
        "--exclude-raw-stables",
        action="store_true",
        help="Skip stablecoin symbols in raw backtest (default: include them)",
    )
    args = p.parse_args()

    start_ts = _ts_hours_ago(args.hours)
    cfg = BacktestConfig(
        min_confidence=args.min_confidence,
        horizon_minutes=args.horizon_minutes,
        fee_bps_per_side=args.fee_bps,
        max_open_positions=args.max_open,
        raw_include_stables=not args.exclude_raw_stables,
        only_matured_signals=not args.include_unmatured,
    )
    db_path = resolved_database_path()
    raw_agents = tuple(a.strip() for a in args.raw_agents.split(",") if a.strip())
    if args.mode == "aggregator":
        result = run_aggregator_backtest(db_path, start_ts=start_ts, cfg=cfg)
    elif args.mode == "raw":
        result = run_raw_signals_backtest(
            db_path, start_ts=start_ts, cfg=cfg, agent_types=raw_agents
        )
    else:
        result = run_backtest_compare(
            db_path, start_ts=start_ts, cfg=cfg, raw_agent_types=raw_agents
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
