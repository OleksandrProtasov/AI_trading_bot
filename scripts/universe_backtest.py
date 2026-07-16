"""Compare replay backtest stats for current symbol universe."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from typing import Any, Dict


def main() -> None:
    p = argparse.ArgumentParser(description="Replay backtest summary by symbol")
    p.add_argument("--hours", type=int, default=168)
    p.add_argument("--horizon-minutes", type=int, default=30)
    args = p.parse_args()

    cmd = [
        sys.executable,
        "historical_replay_backtest.py",
        "--hours",
        str(args.hours),
        "--horizon-minutes",
        str(args.horizon_minutes),
        "--min-confidence",
        "0.58",
        "--min-score",
        "0.35",
        "--min-margin",
        "0.12",
        "--dedup-sec",
        "40",
    ]
    out = subprocess.check_output(cmd, text=True)
    data: Dict[str, Any] = json.loads(out)
    bt = data.get("backtest") or {}
    print("=" * 60)
    print(f"UNIVERSE BACKTEST ({args.hours}h) — leverage: NONE (1x spot-style)")
    print("=" * 60)
    print(f"replayed_aggregator_signals: {data.get('replayed_aggregator_signals')}")
    print(f"trades: {bt.get('trades')}")
    print(f"win_rate_pct: {bt.get('win_rate_pct')}")
    print(f"total_return_pct: {bt.get('total_return_pct')}")
    print(f"max_drawdown_pct: {bt.get('max_drawdown_pct')}")
    skipped = bt.get("skipped") or {}
    print(f"skipped_overlap: {skipped.get('blocked_overlap')}")
    print(f"skipped_allocator: {skipped.get('allocator_filtered')}")
    by_sym = bt.get("trades_by_symbol") or {}
    if by_sym:
        print(f"\nTrades by symbol ({len(by_sym)} coins):")
        for sym, n in sorted(by_sym.items(), key=lambda x: -x[1]):
            print(f"  {sym}: {n}")
    for t in (bt.get("last_trades") or [])[-5:]:
        print(
            f"  last {t.get('symbol')} {t.get('action')} "
            f"ret={float(t.get('net_return_pct',0)):.2f}%"
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
