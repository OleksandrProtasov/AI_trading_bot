"""Backtest strategy on historical charts (replay + structure gate)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List


def _run_replay(*, hours: int, structure_gate: bool) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "historical_replay_backtest.py",
        "--hours",
        str(hours),
        "--horizon-minutes",
        "30",
        "--min-confidence",
        "0.58",
        "--min-score",
        "0.35",
        "--min-margin",
        "0.12",
        "--dedup-sec",
        "40",
    ]
    if structure_gate:
        cmd.append("--structure-gate")
    else:
        cmd.append("--no-structure-gate")
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def _line(label: str, data: Dict[str, Any]) -> str:
    bt = data.get("backtest") or {}
    return (
        f"{label:>12}: trades={bt.get('trades', 0):>3}  "
        f"WR={float(bt.get('win_rate_pct', 0)):>5.1f}%  "
        f"ret={float(bt.get('total_return_pct', 0)):>+7.2f}%  "
        f"DD={float(bt.get('max_drawdown_pct', 0)):>5.2f}%  "
        f"signals={data.get('replayed_aggregator_signals', 0):>4}  "
        f"struct_blk={data.get('structure_filtered_signals', 0):>4}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Chart backtest across time windows")
    p.add_argument(
        "--windows",
        type=str,
        default="168,720,2160",
        help="Comma-separated hours (default 7d,30d,90d)",
    )
    p.add_argument(
        "--compare-structure",
        action="store_true",
        help="Also run without structure gate for comparison",
    )
    args = p.parse_args()
    windows: List[int] = [int(x.strip()) for x in args.windows.split(",") if x.strip()]

    print("=" * 72)
    print("CHART BACKTEST — historical candles + replay (1x, no leverage)")
    print("=" * 72)

    for hours in windows:
        days = hours / 24.0
        print(f"\n--- {hours}h (~{days:.0f}d) WITH structure gate (live-like) ---")
        with_gate = _run_replay(hours=hours, structure_gate=True)
        print(_line("live-like", with_gate))
        by_sym = (with_gate.get("backtest") or {}).get("trades_by_symbol") or {}
        if by_sym:
            parts = [f"{k}:{v}" for k, v in sorted(by_sym.items(), key=lambda x: -x[1])[:8]]
            print("  coins:", ", ".join(parts))

        if args.compare_structure:
            print(f"--- {hours}h WITHOUT structure gate ---")
            no_gate = _run_replay(hours=hours, structure_gate=False)
            print(_line("no struct", no_gate))

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
