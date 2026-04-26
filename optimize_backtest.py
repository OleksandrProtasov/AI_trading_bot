"""Grid search over backtest params to find best historical monthly return proxy."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from itertools import product
from typing import Any, Dict, List, Sequence, Tuple

from core.backtest_portfolio import (
    BacktestConfig,
    DEFAULT_RAW_AGENT_TYPES,
    run_aggregator_backtest,
    run_raw_signals_backtest,
)
from core.runtime_paths import resolved_database_path


def _hours_ago_ts(hours: int) -> int:
    return int((datetime.utcnow() - timedelta(hours=hours)).timestamp())


def _score(res: Dict[str, Any]) -> float:
    # prioritize returns with drawdown penalty; hard reject no-trade runs
    trades = int(res.get("trades", 0) or 0)
    if trades <= 0:
        return -1e9
    ret = float(res.get("total_return_pct", 0.0) or 0.0)
    dd = float(res.get("max_drawdown_pct", 0.0) or 0.0)
    return ret - 0.5 * dd


def _extract_monthly(res: Dict[str, Any]) -> Tuple[float, int]:
    monthly = res.get("monthly_return_pct") or {}
    vals = [float(v) for v in monthly.values()]
    if not vals:
        return 0.0, 0
    return sum(vals) / len(vals), len(vals)


def _run_grid(
    mode: str,
    db_path: str,
    start_ts: int,
    min_confidences: Sequence[float],
    horizons: Sequence[int],
    fees: Sequence[float],
    *,
    raw_agents: Sequence[str],
    raw_include_stables: bool,
    only_matured_signals: bool,
    entry_gap_sec: int,
    symbol_cooldown_sec: int,
    max_trades_per_symbol: int,
    max_open_positions: int,
    emergency_quota_per_hour: int,
    cooloff_loss_streak: int,
    cooloff_skip_entries: int,
    cooloff_override_confidence: float,
    cooloff_override_confirmations: int,
) -> Dict[str, Any]:
    best: Dict[str, Any] | None = None
    checked = 0
    for min_c, h, fee in product(min_confidences, horizons, fees):
        cfg = BacktestConfig(
            min_confidence=min_c,
            horizon_minutes=h,
            fee_bps_per_side=fee,
            max_open_positions=max_open_positions,
            raw_include_stables=raw_include_stables,
            only_matured_signals=only_matured_signals,
            min_gap_between_entries_sec=entry_gap_sec,
            per_symbol_cooldown_sec=symbol_cooldown_sec,
            max_trades_per_symbol=max_trades_per_symbol,
            emergency_trade_quota_per_hour=emergency_quota_per_hour,
            loss_streak_for_cooloff=cooloff_loss_streak,
            cooloff_skipped_entries=cooloff_skip_entries,
            cooloff_override_confidence=cooloff_override_confidence,
            cooloff_override_confirmations=cooloff_override_confirmations,
        )
        if mode == "aggregator":
            res = run_aggregator_backtest(db_path, start_ts=start_ts, cfg=cfg)
        else:
            res = run_raw_signals_backtest(
                db_path, start_ts=start_ts, cfg=cfg, agent_types=raw_agents
            )
        checked += 1
        item = {
            "params": {
                "min_confidence": min_c,
                "horizon_minutes": h,
                "fee_bps": fee,
            },
            "result": res,
            "score": _score(res),
        }
        if best is None or item["score"] > best["score"]:
            best = item

    assert best is not None
    avg_monthly, months = _extract_monthly(best["result"])
    return {
        "mode": mode,
        "checked": checked,
        "best": best,
        "best_avg_monthly_return_pct": avg_monthly,
        "best_months_count": months,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Optimize backtest params on local DB")
    p.add_argument("--hours", type=int, default=24 * 365)
    p.add_argument("--min-confidences", type=str, default="0.4,0.5,0.55,0.6,0.7")
    p.add_argument("--horizons", type=str, default="30,60,120,240,360")
    p.add_argument("--fees", type=str, default="2,5,10")
    p.add_argument("--raw-agents", type=str, default=",".join(DEFAULT_RAW_AGENT_TYPES))
    p.add_argument("--exclude-raw-stables", action="store_true")
    p.add_argument(
        "--include-unmatured",
        action="store_true",
        help="Include fresh signals that have not reached full horizon",
    )
    p.add_argument("--entry-gap-sec", type=int, default=180)
    p.add_argument("--symbol-cooldown-sec", type=int, default=900)
    p.add_argument("--max-trades-per-symbol", type=int, default=3)
    p.add_argument("--max-open", type=int, default=1)
    p.add_argument("--emergency-quota-per-hour", type=int, default=1)
    p.add_argument("--cooloff-loss-streak", type=int, default=2)
    p.add_argument("--cooloff-skip-entries", type=int, default=2)
    p.add_argument("--cooloff-override-confidence", type=float, default=0.9)
    p.add_argument("--cooloff-override-confirmations", type=int, default=3)
    args = p.parse_args()

    mins = [float(x) for x in args.min_confidences.split(",") if x.strip()]
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    fees = [float(x) for x in args.fees.split(",") if x.strip()]
    raw_agents = tuple(x.strip() for x in args.raw_agents.split(",") if x.strip())
    start_ts = _hours_ago_ts(args.hours)
    db_path = resolved_database_path()

    out = {
        "note": "Historical optimization only; no guarantee of future 10%/month.",
        "window_hours": args.hours,
        "aggregator": _run_grid(
            "aggregator",
            db_path,
            start_ts,
            mins,
            horizons,
            fees,
            raw_agents=raw_agents,
            raw_include_stables=not args.exclude_raw_stables,
            only_matured_signals=not args.include_unmatured,
            entry_gap_sec=args.entry_gap_sec,
            symbol_cooldown_sec=args.symbol_cooldown_sec,
            max_trades_per_symbol=args.max_trades_per_symbol,
            max_open_positions=args.max_open,
            emergency_quota_per_hour=args.emergency_quota_per_hour,
            cooloff_loss_streak=args.cooloff_loss_streak,
            cooloff_skip_entries=args.cooloff_skip_entries,
            cooloff_override_confidence=args.cooloff_override_confidence,
            cooloff_override_confirmations=args.cooloff_override_confirmations,
        ),
        "raw": _run_grid(
            "raw",
            db_path,
            start_ts,
            mins,
            horizons,
            fees,
            raw_agents=raw_agents,
            raw_include_stables=not args.exclude_raw_stables,
            only_matured_signals=not args.include_unmatured,
            entry_gap_sec=args.entry_gap_sec,
            symbol_cooldown_sec=args.symbol_cooldown_sec,
            max_trades_per_symbol=args.max_trades_per_symbol,
            max_open_positions=args.max_open,
            emergency_quota_per_hour=args.emergency_quota_per_hour,
            cooloff_loss_streak=args.cooloff_loss_streak,
            cooloff_skip_entries=args.cooloff_skip_entries,
            cooloff_override_confidence=args.cooloff_override_confidence,
            cooloff_override_confirmations=args.cooloff_override_confirmations,
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
