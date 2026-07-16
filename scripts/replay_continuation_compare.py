"""Compare analyst replay: retest-only vs retest+continuation on historical DB."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(label: str, hours: int, *, continuation: bool, deposit: float, leverage: float) -> dict:
    cmd = [
        sys.executable,
        str(ROOT / "historical_replay_backtest.py"),
        "--hours",
        str(hours),
        "--horizon-minutes",
        "120",
        "--min-confidence",
        "0.58",
        "--fee-bps",
        "2",
        "--deposit-eur",
        str(deposit),
        "--leverage",
        str(leverage),
        "--analyst-mode",
        "--analyst-ready-min",
        "72",
        "--analyst-min-win",
        "0.45",
    ]
    if continuation:
        cmd.append("--continuation")
    else:
        cmd.append("--no-continuation")

    env = os.environ.copy()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        return {
            "label": label,
            "error": (proc.stderr or proc.stdout or "replay failed")[-500:],
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"label": label, "error": "invalid JSON from replay"}
    data["label"] = label
    return data


def _print_result(data: dict, deposit: float) -> None:
    label = data.get("label", "?")
    print(f"\n  [{label}]")
    if data.get("error"):
        print(f"  ERROR: {data['error']}")
        return
    modes = data.get("replay_entry_modes") or {}
    ap = data.get("analyst_portfolio") or {}
    bt = data.get("backtest") or {}
    print(
        f"  signals: {data.get('replayed_aggregator_signals', 0)} "
        f"(retest={modes.get('retest', 0)}, continuation={modes.get('continuation', 0)})"
    )
    print(f"  filtered by analyst gate: {data.get('analyst_filtered_signals', 0)}")
    if ap.get("error"):
        print(f"  portfolio: {ap['error']}")
    else:
        print(
            f"  trades: {ap.get('trades', 0)} | WR: {ap.get('win_rate_pct', 0)}% | "
            f"{ap.get('starting_eur', deposit):.0f} -> {ap.get('final_eur', deposit):.2f} EUR "
            f"({ap.get('return_pct', 0):+.2f}%)"
        )
    if bt:
        print(
            f"  raw backtest hits: {bt.get('hits', 0)}/{bt.get('evaluated', 0)} "
            f"({bt.get('hit_rate_pct', 0)}%)"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Parallel historical replay comparison")
    p.add_argument("--hours", type=int, default=168, help="Lookback window (default 7d)")
    p.add_argument("--deposit", type=float, default=100.0)
    p.add_argument("--leverage", type=float, default=10.0)
    p.add_argument("--parallel", action="store_true", help="Run both replays in parallel")
    args = p.parse_args()

    jobs = [
        ("retest only (>=72)", False),
        ("retest + continuation (live)", True),
    ]

    print("=" * 72)
    print(f"HISTORICAL REPLAY | {args.hours}h | {args.deposit:.0f} EUR | {args.leverage:.0f}x")
    print("Uses crypto_analytics.db — safe while main.py is running (temp copy).")
    print("=" * 72)

    results: list[dict] = []
    if args.parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = {
                pool.submit(
                    _run, label, args.hours, continuation=cont, deposit=args.deposit, leverage=args.leverage
                ): label
                for label, cont in jobs
            }
            for fut in as_completed(futs):
                results.append(fut.result())
    else:
        for label, cont in jobs:
            results.append(
                _run(label, args.hours, continuation=cont, deposit=args.deposit, leverage=args.leverage)
            )

    for data in results:
        _print_result(data, args.deposit)

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
