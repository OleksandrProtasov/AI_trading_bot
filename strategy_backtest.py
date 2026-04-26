"""Compare strategy profiles on historical signals."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

from core.backtest_portfolio import BacktestConfig, run_backtest_compare
from core.runtime_paths import resolved_database_path


def _hours_ago_ts(hours: int) -> int:
    return int((datetime.utcnow() - timedelta(hours=hours)).timestamp())


def _snapshot(res: dict) -> dict:
    if "error" in res:
        return {"error": res["error"]}
    return {
        "trades": res.get("trades", 0),
        "return_pct": res.get("total_return_pct", 0.0),
        "drawdown_pct": res.get("max_drawdown_pct", 0.0),
        "win_rate_pct": res.get("win_rate_pct", 0.0),
        "skipped": res.get("skipped", {}),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Compare strategy modes on backtests")
    p.add_argument("--hours", type=int, default=24 * 7)
    p.add_argument("--horizon-minutes", type=int, default=15)
    p.add_argument("--fee-bps", type=float, default=2.0)
    p.add_argument("--min-confidence", type=float, default=0.4)
    p.add_argument("--max-open", type=int, default=1)
    p.add_argument("--entry-gap-sec", type=int, default=180)
    p.add_argument("--symbol-cooldown-sec", type=int, default=900)
    p.add_argument("--max-trades-per-symbol", type=int, default=3)
    p.add_argument("--emergency-quota-per-hour", type=int, default=1)
    p.add_argument("--cooloff-loss-streak", type=int, default=2)
    p.add_argument("--cooloff-skip-entries", type=int, default=2)
    p.add_argument("--cooloff-override-confidence", type=float, default=0.9)
    p.add_argument("--cooloff-override-confirmations", type=int, default=3)
    p.add_argument("--raw-agents", type=str, default="market,liquidity,onchain,emergency,shitcoin")
    args = p.parse_args()

    start_ts = _hours_ago_ts(args.hours)
    db = resolved_database_path()
    agents = tuple(a.strip() for a in args.raw_agents.split(",") if a.strip())

    out = {"window_hours": args.hours, "horizon_minutes": args.horizon_minutes, "modes": {}}
    for mode in ("balanced", "trend_following", "defensive"):
        cfg = BacktestConfig(
            min_confidence=args.min_confidence,
            strategy_min_confidence=args.min_confidence,
            strategy_mode=mode,
            horizon_minutes=args.horizon_minutes,
            fee_bps_per_side=args.fee_bps,
            max_open_positions=args.max_open,
            raw_include_stables=True,
            min_gap_between_entries_sec=args.entry_gap_sec,
            per_symbol_cooldown_sec=args.symbol_cooldown_sec,
            max_trades_per_symbol=args.max_trades_per_symbol,
            emergency_trade_quota_per_hour=args.emergency_quota_per_hour,
            loss_streak_for_cooloff=args.cooloff_loss_streak,
            cooloff_skipped_entries=args.cooloff_skip_entries,
            cooloff_override_confidence=args.cooloff_override_confidence,
            cooloff_override_confirmations=args.cooloff_override_confirmations,
        )
        res = run_backtest_compare(db, start_ts=start_ts, cfg=cfg, raw_agent_types=agents)
        out["modes"][mode] = {
            "aggregator": _snapshot(res.get("aggregator", {})),
            "raw": _snapshot(res.get("raw", {})),
        }

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
