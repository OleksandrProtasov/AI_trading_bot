"""Walk-forward evaluation for replay EV configs across multiple windows."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from itertools import product
from typing import Any, Dict, List, Tuple


def _run_window(
    *,
    end_ts: int,
    window_hours: int,
    horizon_minutes: int,
    min_confidence: float,
    fee_bps: float,
    max_open: int,
    entry_gap_sec: int,
    symbol_cooldown_sec: int,
    max_trades_per_symbol: int,
    slippage_bps: float,
    ev_buffer_bps: float,
    ev_confidence_mult: float,
    ev_margin_mult: float,
    ev_source_mult: float,
    ev_bearish_penalty_mult: float,
    ev_emergency_penalty_mult: float,
    ev_conflict_penalty_mult: float,
    recent_window_sec: int,
    min_score: float,
    min_margin: float,
    dedup_sec: int,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        "historical_replay_backtest.py",
        "--hours",
        str(window_hours),
        "--end-ts",
        str(end_ts),
        "--horizon-minutes",
        str(horizon_minutes),
        "--recent-window-sec",
        str(recent_window_sec),
        "--min-score",
        str(min_score),
        "--min-margin",
        str(min_margin),
        "--dedup-sec",
        str(dedup_sec),
        "--min-confidence",
        str(min_confidence),
        "--fee-bps",
        str(fee_bps),
        "--max-open",
        str(max_open),
        "--entry-gap-sec",
        str(entry_gap_sec),
        "--symbol-cooldown-sec",
        str(symbol_cooldown_sec),
        "--max-trades-per-symbol",
        str(max_trades_per_symbol),
        "--slippage-bps",
        str(slippage_bps),
        "--ev-buffer-bps",
        str(ev_buffer_bps),
        "--ev-confidence-mult",
        str(ev_confidence_mult),
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
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def _bootstrap_profiles(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """From stricter to looser replay generation settings."""
    return [
        {
            "recent_window_sec": args.recent_window_sec,
            "min_score": args.min_score,
            "min_margin": args.min_margin,
            "dedup_sec": args.dedup_sec,
            "min_confidence": args.min_confidence,
        },
        {
            "recent_window_sec": max(args.recent_window_sec, 180),
            "min_score": min(args.min_score, 0.22),
            "min_margin": min(args.min_margin, 0.06),
            "dedup_sec": min(args.dedup_sec, 30),
            "min_confidence": min(args.min_confidence, 0.38),
        },
        {
            "recent_window_sec": max(args.recent_window_sec, 240),
            "min_score": min(args.min_score, 0.18),
            "min_margin": min(args.min_margin, 0.03),
            "dedup_sec": min(args.dedup_sec, 20),
            "min_confidence": min(args.min_confidence, 0.35),
        },
        {
            "recent_window_sec": max(args.recent_window_sec, 300),
            "min_score": min(args.min_score, 0.12),
            "min_margin": min(args.min_margin, 0.01),
            "dedup_sec": min(args.dedup_sec, 10),
            "min_confidence": min(args.min_confidence, 0.30),
        },
    ]


def _run_window_bootstrapped(
    *,
    args: argparse.Namespace,
    end_ts: int,
    window_hours: int,
    horizon_minutes: int,
    fee_bps: float,
    max_open: int,
    entry_gap_sec: int,
    symbol_cooldown_sec: int,
    max_trades_per_symbol: int,
    slippage_bps: float,
    ev_buffer_bps: float,
    ev_confidence_mult: float,
    ev_margin_mult: float,
    ev_source_mult: float,
    ev_bearish_penalty_mult: float,
    ev_emergency_penalty_mult: float,
    ev_conflict_penalty_mult: float,
    min_trades_per_window: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Try multiple replay-generation profiles until we get enough trades.
    Returns selected result and profile metadata.
    """
    profiles = _bootstrap_profiles(args)
    last_res: Dict[str, Any] = {"backtest": {"error": "no_profiles"}}
    last_profile = profiles[-1]
    for idx, p in enumerate(profiles):
        res = _run_window(
            end_ts=end_ts,
            window_hours=window_hours,
            horizon_minutes=horizon_minutes,
            recent_window_sec=int(p["recent_window_sec"]),
            min_score=float(p["min_score"]),
            min_margin=float(p["min_margin"]),
            dedup_sec=int(p["dedup_sec"]),
            min_confidence=float(p["min_confidence"]),
            fee_bps=fee_bps,
            max_open=max_open,
            entry_gap_sec=entry_gap_sec,
            symbol_cooldown_sec=symbol_cooldown_sec,
            max_trades_per_symbol=max_trades_per_symbol,
            slippage_bps=slippage_bps,
            ev_buffer_bps=ev_buffer_bps,
            ev_confidence_mult=ev_confidence_mult,
            ev_margin_mult=ev_margin_mult,
            ev_source_mult=ev_source_mult,
            ev_bearish_penalty_mult=ev_bearish_penalty_mult,
            ev_emergency_penalty_mult=ev_emergency_penalty_mult,
            ev_conflict_penalty_mult=ev_conflict_penalty_mult,
        )
        backtest = res.get("backtest") or {}
        trades = int(backtest.get("trades", 0) or 0)
        if "error" not in backtest and trades >= min_trades_per_window:
            return res, {"profile_index": idx, **p}
        last_res = res
        last_profile = p
    return last_res, {"profile_index": len(profiles) - 1, **last_profile}


def _score_window(backtest: Dict[str, Any]) -> float:
    trades = int(backtest.get("trades", 0) or 0)
    if trades <= 0:
        return -1e9
    ret = float(backtest.get("total_return_pct", 0.0) or 0.0)
    dd = float(backtest.get("max_drawdown_pct", 0.0) or 0.0)
    win = float(backtest.get("win_rate_pct", 0.0) or 0.0)
    return ret - 0.6 * dd + 0.02 * win


def _aggregate_metrics(windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals = [
        w.get("backtest", {})
        for w in windows
        if "error" not in (w.get("backtest", {}) or {})
    ]
    if not vals:
        return {"error": "no_windows"}
    n = len(vals)
    ret = [float(v.get("total_return_pct", 0.0) or 0.0) for v in vals]
    dd = [float(v.get("max_drawdown_pct", 0.0) or 0.0) for v in vals]
    wr = [float(v.get("win_rate_pct", 0.0) or 0.0) for v in vals]
    tr = [int(v.get("trades", 0) or 0) for v in vals]
    prof_windows = sum(1 for x in ret if x > 0)
    return {
        "windows": n,
        "avg_return_pct": sum(ret) / n,
        "avg_drawdown_pct": sum(dd) / n,
        "avg_win_rate_pct": sum(wr) / n,
        "avg_trades": sum(tr) / n,
        "profitable_windows_pct": (prof_windows / n) * 100.0,
    }


def _now_ts() -> int:
    return int(time.time())


def _time_exceeded(deadline_ts: float | None) -> bool:
    return deadline_ts is not None and time.time() >= deadline_ts


def _evaluate_for_step(
    *,
    args: argparse.Namespace,
    end_points: List[int],
    ev_buffers: List[float],
    ev_conf: List[float],
    ev_margin: List[float],
    ev_source: List[float],
    ev_bear: List[float],
    ev_emg: List[float],
    ev_conflict: List[float],
    deadline_ts: float | None,
) -> Tuple[List[Dict[str, Any]], int, bool]:
    items: List[Dict[str, Any]] = []
    checked = 0
    timed_out = False
    for vals in product(
        ev_buffers, ev_conf, ev_margin, ev_source, ev_bear, ev_emg, ev_conflict
    ):
        if _time_exceeded(deadline_ts):
            timed_out = True
            break
        (
            ev_buffer_bps,
            ev_confidence_mult,
            ev_margin_mult,
            ev_source_mult,
            ev_bearish_penalty_mult,
            ev_emergency_penalty_mult,
            ev_conflict_penalty_mult,
        ) = vals
        checked += 1
        window_results = []
        failed = None
        for end_ts in end_points:
            if _time_exceeded(deadline_ts):
                timed_out = True
                break
            try:
                res, used_profile = _run_window_bootstrapped(
                    args=args,
                    end_ts=end_ts,
                    window_hours=args.window_hours,
                    horizon_minutes=args.horizon_minutes,
                    fee_bps=args.fee_bps,
                    max_open=args.max_open,
                    entry_gap_sec=args.entry_gap_sec,
                    symbol_cooldown_sec=args.symbol_cooldown_sec,
                    max_trades_per_symbol=args.max_trades_per_symbol,
                    slippage_bps=args.slippage_bps,
                    ev_buffer_bps=ev_buffer_bps,
                    ev_confidence_mult=ev_confidence_mult,
                    ev_margin_mult=ev_margin_mult,
                    ev_source_mult=ev_source_mult,
                    ev_bearish_penalty_mult=ev_bearish_penalty_mult,
                    ev_emergency_penalty_mult=ev_emergency_penalty_mult,
                    ev_conflict_penalty_mult=ev_conflict_penalty_mult,
                    min_trades_per_window=int(args.min_trades_per_window),
                )
                backtest = res.get("backtest") or {}
                window_results.append(
                    {
                        "end_ts": end_ts,
                        "end_utc": datetime.utcfromtimestamp(end_ts).isoformat() + "Z",
                        "score": _score_window(backtest),
                        "backtest": backtest,
                        "bootstrap_profile": used_profile,
                    }
                )
            except Exception as exc:
                failed = str(exc)
                break
        if timed_out:
            break

        params = {
            "ev_buffer_bps": ev_buffer_bps,
            "ev_confidence_mult": ev_confidence_mult,
            "ev_margin_mult": ev_margin_mult,
            "ev_source_mult": ev_source_mult,
            "ev_bearish_penalty_mult": ev_bearish_penalty_mult,
            "ev_emergency_penalty_mult": ev_emergency_penalty_mult,
            "ev_conflict_penalty_mult": ev_conflict_penalty_mult,
        }
        if failed:
            items.append({"params": params, "error": failed, "score": -1e9})
            continue

        agg = _aggregate_metrics(window_results)
        if "error" in agg or int(agg.get("windows", 0)) < int(args.min_active_windows):
            items.append(
                {
                    "params": params,
                    "aggregate": agg,
                    "window_results": window_results,
                    "score": -1e9,
                    "invalid_reason": f"active_windows<{args.min_active_windows}",
                }
            )
            continue
        total_score = (
            float(agg["avg_return_pct"])
            - 0.6 * float(agg["avg_drawdown_pct"])
            + 0.03 * float(agg["profitable_windows_pct"])
        )
        items.append(
            {
                "params": params,
                "aggregate": agg,
                "window_results": window_results,
                "score": total_score,
            }
        )
    return items, checked, timed_out


def main() -> None:
    p = argparse.ArgumentParser(description="Walk-forward replay EV evaluation")
    p.add_argument("--window-hours", type=int, default=240)
    p.add_argument("--step-hours", type=int, default=72)
    p.add_argument(
        "--auto-step-hours",
        type=str,
        default="",
        help="Optional comma list for adaptive step search, e.g. 168,72,48,24,12",
    )
    p.add_argument("--windows", type=int, default=4)
    p.add_argument(
        "--max-runtime-sec",
        type=int,
        default=0,
        help="Hard time budget for this run. 0 = unlimited.",
    )
    p.add_argument("--end-ts", type=int, default=None)
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--recent-window-sec", type=int, default=60)
    p.add_argument("--min-score", type=float, default=0.3)
    p.add_argument("--min-margin", type=float, default=0.1)
    p.add_argument("--dedup-sec", type=int, default=60)
    p.add_argument("--min-confidence", type=float, default=0.4)
    p.add_argument("--fee-bps", type=float, default=2.0)
    p.add_argument("--max-open", type=int, default=3)
    p.add_argument("--entry-gap-sec", type=int, default=120)
    p.add_argument("--symbol-cooldown-sec", type=int, default=600)
    p.add_argument("--max-trades-per-symbol", type=int, default=4)
    p.add_argument("--slippage-bps", type=float, default=3.0)

    # EV search space (compact by default)
    p.add_argument("--ev-buffers", type=str, default="8")
    p.add_argument("--ev-confidence-mults", type=str, default="18,20")
    p.add_argument("--ev-margin-mults", type=str, default="20,22")
    p.add_argument("--ev-source-mults", type=str, default="2,3")
    p.add_argument("--ev-bearish-penalty-mults", type=str, default="6")
    p.add_argument("--ev-emergency-penalty-mults", type=str, default="4")
    p.add_argument("--ev-conflict-penalty-mults", type=str, default="25")
    p.add_argument("--top", type=int, default=3)
    p.add_argument(
        "--min-active-windows",
        type=int,
        default=2,
        help="Minimum non-empty windows required for a valid config score",
    )
    p.add_argument(
        "--min-trades-per-window",
        type=int,
        default=20,
        help="Bootstrap target: minimum trades required per window",
    )
    args = p.parse_args()

    base_end = args.end_ts if args.end_ts is not None else _now_ts()
    step_candidates: List[int]
    if args.auto_step_hours.strip():
        step_candidates = [
            int(x) for x in args.auto_step_hours.split(",") if x.strip()
        ]
    else:
        step_candidates = [int(args.step_hours)]

    ev_buffers = [float(x) for x in args.ev_buffers.split(",") if x.strip()]
    ev_conf = [float(x) for x in args.ev_confidence_mults.split(",") if x.strip()]
    ev_margin = [float(x) for x in args.ev_margin_mults.split(",") if x.strip()]
    ev_source = [float(x) for x in args.ev_source_mults.split(",") if x.strip()]
    ev_bear = [float(x) for x in args.ev_bearish_penalty_mults.split(",") if x.strip()]
    ev_emg = [float(x) for x in args.ev_emergency_penalty_mults.split(",") if x.strip()]
    ev_conflict = [float(x) for x in args.ev_conflict_penalty_mults.split(",") if x.strip()]

    items: List[Dict[str, Any]] = []
    checked = 0
    selected_step = step_candidates[0]
    timed_out = False
    started_at = time.time()
    deadline_ts: float | None = None
    if int(args.max_runtime_sec) > 0:
        deadline_ts = started_at + int(args.max_runtime_sec)
    for step_h in step_candidates:
        if _time_exceeded(deadline_ts):
            timed_out = True
            break
        end_points = [base_end - i * step_h * 3600 for i in range(args.windows)]
        candidate_items, candidate_checked, candidate_timed_out = _evaluate_for_step(
            args=args,
            end_points=end_points,
            ev_buffers=ev_buffers,
            ev_conf=ev_conf,
            ev_margin=ev_margin,
            ev_source=ev_source,
            ev_bear=ev_bear,
            ev_emg=ev_emg,
            ev_conflict=ev_conflict,
            deadline_ts=deadline_ts,
        )
        checked += candidate_checked
        if candidate_timed_out:
            timed_out = True
        ranked_candidate = sorted(
            candidate_items, key=lambda x: float(x.get("score", -1e9)), reverse=True
        )
        if ranked_candidate and float(ranked_candidate[0].get("score", -1e9)) > -1e9:
            items = candidate_items
            selected_step = step_h
            break
        # Keep latest attempt for diagnostics if none is valid.
        items = candidate_items
        selected_step = step_h
        if timed_out:
            break

    ranked = sorted(items, key=lambda x: float(x.get("score", -1e9)), reverse=True)
    top_n = max(1, int(args.top))
    out = {
        "note": "Walk-forward replay EV ranking (historical only).",
        "checked": checked,
        "timed_out": timed_out,
        "elapsed_sec": round(time.time() - started_at, 2),
        "window_hours": args.window_hours,
        "step_hours": selected_step,
        "auto_step_hours": step_candidates,
        "windows": args.windows,
        "replay_generation": {
            "recent_window_sec": args.recent_window_sec,
            "min_score": args.min_score,
            "min_margin": args.min_margin,
            "dedup_sec": args.dedup_sec,
            "min_trades_per_window": args.min_trades_per_window,
        },
        "search_space": {
            "ev_buffers": ev_buffers,
            "ev_confidence_mults": ev_conf,
            "ev_margin_mults": ev_margin,
            "ev_source_mults": ev_source,
            "ev_bearish_penalty_mults": ev_bear,
            "ev_emergency_penalty_mults": ev_emg,
            "ev_conflict_penalty_mults": ev_conflict,
        },
        "best": ranked[0] if ranked else None,
        "top": ranked[:top_n],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
