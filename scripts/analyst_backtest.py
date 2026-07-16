"""Analyst backtest: 100 EUR, 10x leverage, confidence/quality sizing."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _run(
    hours: int,
    deposit: float,
    leverage: float,
    *,
    analyst: bool = False,
    confidence_sizing: bool = False,
) -> dict:
    cmd = [
        sys.executable,
        "historical_replay_backtest.py",
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
        "--no-structure-gate",
    ]
    if analyst:
        cmd.append("--analyst-mode")
    else:
        cmd.append("--no-analyst-mode")
    if confidence_sizing:
        cmd.append("--confidence-sizing")
    return json.loads(subprocess.check_output(cmd, text=True))


def _print_block(title: str, data: dict, deposit: float) -> None:
    ap = data.get("analyst_portfolio") or {}
    print(f"\n  [{title}]")
    if ap.get("error"):
        print(f"  {ap['error']}")
        print(f"  replay signals: {data.get('replayed_aggregator_signals', 0)}")
        return
    print(f"  replay signals: {data.get('replayed_aggregator_signals', 0)}")
    print(f"  trades: {ap.get('trades', 0)}  |  WR: {ap.get('win_rate_pct', 0)}%")
    print(f"  {ap.get('starting_eur', deposit):.2f} -> {ap.get('final_eur', deposit):.2f} EUR")
    print(
        f"  P/L: {ap.get('profit_eur', 0):+.2f} EUR "
        f"({ap.get('return_pct', 0):+.2f}%)  |  DD: {ap.get('max_drawdown_pct', 0):.2f}%"
    )
    for t in (ap.get("last_trades") or [])[-3:]:
        print(
            f"    {t.get('symbol')} {t.get('action')} margin={t.get('position_pct', 0):.1f}% "
            f"pnl={t.get('pnl_eur', 0):+.2f} EUR"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--deposit", type=float, default=100.0)
    p.add_argument("--leverage", type=float, default=10.0)
    args = p.parse_args()

    print("=" * 70)
    print(f"BACKTEST | {args.deposit:.0f} EUR | leverage {args.leverage:.0f}x")
    print("High confidence -> 10% margin | medium -> 1-5%")
    print("=" * 70)

    for hours, label in ((168, "7d"), (720, "30d"), (2160, "90d")):
        print(f"\n--- {label} ---")
        strict = _run(
            hours, args.deposit, args.leverage, analyst=True
        )
        conf = _run(
            hours, args.deposit, args.leverage, confidence_sizing=True
        )
        _print_block("LIVE analyst (SMC ready >=72)", strict, args.deposit)
        _print_block("Confidence sizing (historical baseline)", conf, args.deposit)

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
