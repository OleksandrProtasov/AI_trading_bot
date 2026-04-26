"""Grid-search EV-gate parameters on historical replay backtest."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from itertools import product
from typing import Any, Dict, List


def _score(res: Dict[str, Any]) -> float:
    backtest = res.get("backtest") or {}
    trades = int(backtest.get("trades", 0) or 0)
    if trades <= 0:
        return -1e9
    ret = float(backtest.get("total_return_pct", 0.0) or 0.0)
    dd = float(backtest.get("max_drawdown_pct", 0.0) or 0.0)
    # Favor positive return and lower drawdown.
    return ret - 0.6 * dd


def _run_once(
    args: argparse.Namespace,
    *,
    ev_buffer: float,
    ev_conf_mult: float,
    ev_margin_mult: float,
    ev_source_mult: float,
    ev_bearish_penalty_mult: float,
    ev_emergency_penalty_mult: float,
    ev_conflict_penalty_mult: float,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "historical_replay_backtest.py",
        "--hours",
        str(args.hours),
        "--horizon-minutes",
        str(args.horizon_minutes),
        "--min-confidence",
        str(args.min_confidence),
        "--fee-bps",
        str(args.fee_bps),
        "--max-open",
        str(args.max_open),
        "--entry-gap-sec",
        str(args.entry_gap_sec),
        "--symbol-cooldown-sec",
        str(args.symbol_cooldown_sec),
        "--max-trades-per-symbol",
        str(args.max_trades_per_symbol),
        "--slippage-bps",
        str(args.slippage_bps),
        "--ev-buffer-bps",
        str(ev_buffer),
        "--ev-confidence-mult",
        str(ev_conf_mult),
        "--ev-margin-mult",
        str(ev_margin_mult),
        "--ev-source-mult",
        str(ev_source_mult),
        "--ev-bearish-penalty-mult",
        str(ev_bearish_penalty_mult),
        "--ev-emergency-penalty-mult",
        str(ev_emergency_penalty_mult),
        "--ev-conflict-penalty-mult",
        str(ev_conflict_penalty_mult),
    ]
    if args.end_ts is not None:
        cmd.extend(["--end-ts", str(args.end_ts)])
    out = subprocess.check_output(cmd, text=True)
    payload = json.loads(out)
    payload["score"] = _score(payload)
    payload["params"] = {
        "ev_buffer_bps": ev_buffer,
        "ev_confidence_mult": ev_conf_mult,
        "ev_margin_mult": ev_margin_mult,
        "ev_source_mult": ev_source_mult,
        "ev_bearish_penalty_mult": ev_bearish_penalty_mult,
        "ev_emergency_penalty_mult": ev_emergency_penalty_mult,
        "ev_conflict_penalty_mult": ev_conflict_penalty_mult,
    }
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Optimize EV gate parameters on replay backtest")
    p.add_argument("--hours", type=int, default=24 * 30)
    p.add_argument("--end-ts", type=int, default=None)
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--min-confidence", type=float, default=0.4)
    p.add_argument("--fee-bps", type=float, default=2.0)
    p.add_argument("--max-open", type=int, default=3)
    p.add_argument("--entry-gap-sec", type=int, default=120)
    p.add_argument("--symbol-cooldown-sec", type=int, default=600)
    p.add_argument("--max-trades-per-symbol", type=int, default=4)
    p.add_argument("--slippage-bps", type=float, default=3.0)
    p.add_argument(
        "--ev-buffers",
        type=str,
        default="8",
        help="Comma-separated ev_buffer_bps values",
    )
    p.add_argument("--ev-confidence-mults", type=str, default="18,20,22")
    p.add_argument("--ev-margin-mults", type=str, default="18,20,22")
    p.add_argument("--ev-source-mults", type=str, default="2,3")
    p.add_argument("--ev-bearish-penalty-mults", type=str, default="6")
    p.add_argument("--ev-emergency-penalty-mults", type=str, default="4")
    p.add_argument("--ev-conflict-penalty-mults", type=str, default="25")
    p.add_argument("--top", type=int, default=5)
    args = p.parse_args()

    ev_buffers = [float(x) for x in args.ev_buffers.split(",") if x.strip()]
    ev_conf_mults = [float(x) for x in args.ev_confidence_mults.split(",") if x.strip()]
    ev_margin_mults = [float(x) for x in args.ev_margin_mults.split(",") if x.strip()]
    ev_source_mults = [float(x) for x in args.ev_source_mults.split(",") if x.strip()]
    ev_bearish_mults = [float(x) for x in args.ev_bearish_penalty_mults.split(",") if x.strip()]
    ev_emergency_mults = [float(x) for x in args.ev_emergency_penalty_mults.split(",") if x.strip()]
    ev_conflict_mults = [float(x) for x in args.ev_conflict_penalty_mults.split(",") if x.strip()]

    checked = 0
    items: List[Dict[str, Any]] = []
    for (
        ev_buffer,
        ev_conf_mult,
        ev_margin_mult,
        ev_src,
        ev_bear,
        ev_emg,
        ev_conflicts,
    ) in product(
        ev_buffers,
        ev_conf_mults,
        ev_margin_mults,
        ev_source_mults,
        ev_bearish_mults,
        ev_emergency_mults,
        ev_conflict_mults,
    ):
        checked += 1
        try:
            item = _run_once(
                args,
                ev_buffer=ev_buffer,
                ev_conf_mult=ev_conf_mult,
                ev_margin_mult=ev_margin_mult,
                ev_source_mult=ev_src,
                ev_bearish_penalty_mult=ev_bear,
                ev_emergency_penalty_mult=ev_emg,
                ev_conflict_penalty_mult=ev_conflicts,
            )
            items.append(item)
        except Exception as exc:
            items.append(
                {
                    "params": {
                        "ev_buffer_bps": ev_buffer,
                        "ev_confidence_mult": ev_conf_mult,
                        "ev_margin_mult": ev_margin_mult,
                        "ev_source_mult": ev_src,
                        "ev_bearish_penalty_mult": ev_bear,
                        "ev_emergency_penalty_mult": ev_emg,
                        "ev_conflict_penalty_mult": ev_conflicts,
                    },
                    "error": str(exc),
                    "score": -1e9,
                }
            )

    ranked = sorted(items, key=lambda x: float(x.get("score", -1e9)), reverse=True)
    top_n = max(1, int(args.top))
    top_items = ranked[:top_n]

    out = {
        "note": "Replay EV optimization (historical only, no live guarantee).",
        "checked": checked,
        "search_space": {
            "ev_buffers": ev_buffers,
            "ev_confidence_mults": ev_conf_mults,
            "ev_margin_mults": ev_margin_mults,
            "ev_source_mults": ev_source_mults,
            "ev_bearish_penalty_mults": ev_bearish_mults,
            "ev_emergency_penalty_mults": ev_emergency_mults,
            "ev_conflict_penalty_mults": ev_conflict_mults,
        },
        "best": top_items[0] if top_items else None,
        "top": top_items,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
