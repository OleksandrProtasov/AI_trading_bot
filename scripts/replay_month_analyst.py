"""Fast 30d SMC analyst replay via structure scan (live-like, with zone + partial)."""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import config
from core.candle_resample import load_candles_1m_sync, resample_ohlc
from core.liquidity_targets import load_recent_book_zones_sync
from core.partial_exit import simulate_full_trade_exit
from core.research_end_ts import resolve_research_end_ts
from core.runtime_paths import resolved_database_path
from core.smc_analysis import find_swings
from core.strategy_engine import propose_entry
from core.structure_gate import StructureGate
from core.structure_levels import finalize_structure_levels


def main() -> None:
    p = argparse.ArgumentParser(description="Monthly SMC analyst structure-scan replay")
    p.add_argument("--hours", type=int, default=720, help="Lookback (default 30d)")
    p.add_argument("--hold-hours", type=float, default=2.0, help="Max hold (default 120min)")
    p.add_argument("--step-sec", type=int, default=3600, help="Scan step (default 1h)")
    p.add_argument("--max-symbols", type=int, default=80)
    p.add_argument("--cooldown-sec", type=int, default=14400)
    p.add_argument("--deposit", type=float, default=100.0)
    p.add_argument("--leverage", type=float, default=10.0)
    args = p.parse_args()

    db_path = resolved_database_path()
    end_ts = resolve_research_end_ts(db_path, horizon_minutes=int(args.hold_hours * 60))
    since = int(end_ts) - int(args.hours * 3600)
    hold_sec = int(args.hold_hours * 3600)

    cont_on = bool(getattr(config.agent, "agg_structure_continuation_enabled", False))
    gate = StructureGate(continuation_enabled=cont_on)
    cfg = gate._config_from_agent()
    ready_min = int(getattr(config.agent, "analyst_ready_min_score", 78))
    min_win = float(getattr(config.agent, "analyst_min_win_probability", 0.50))
    min_sl = float(getattr(config.agent, "agg_structure_min_sl_pct", 0.55))
    max_sl = float(getattr(config.agent, "agg_structure_max_sl_pct", 2.5))
    min_rr = float(getattr(config.agent, "agg_structure_min_rr", 2.5))

    import sqlite3

    conn = sqlite3.connect(db_path)
    syms = [
        str(r[0]).upper()
        for r in conn.execute(
            """
            SELECT symbol FROM candles
            WHERE timeframe='1m' AND timestamp >= ?
            GROUP BY symbol
            HAVING COUNT(*) > 800
            ORDER BY COUNT(*) DESC
            LIMIT ?
            """,
            (since, int(args.max_symbols)),
        ).fetchall()
    ]
    conn.close()

    trades: list = []
    last_alert: dict[str, int] = {}
    scanned = 0

    print("=" * 72)
    print(
        f"MONTHLY SMC REPLAY | {args.hours}h | {len(syms)} symbols | "
        f"ready>={ready_min} win>={min_win:.0%} | continuation={'on' if cont_on else 'off'}"
    )
    print(
        f"Window: {time.strftime('%Y-%m-%d', time.gmtime(since))} -> "
        f"{time.strftime('%Y-%m-%d', time.gmtime(end_ts))} UTC"
    )
    print("=" * 72)

    for sym in syms:
        ts = since + 3600
        while ts < end_ts - hold_sec:
            scanned += 1
            setup = gate.scan_symbol(db_path, sym, as_of_ts=ts)
            if not setup or setup.state != "ready":
                ts += int(args.step_sec)
                continue
            if setup.checklist.get("continuation") and not cont_on:
                ts += int(args.step_sec)
                continue
            act = "BUY" if setup.side == "long" else "SELL"
            c1m = load_candles_1m_sync(
                db_path, sym, limit=8000, end_ts=ts + hold_sec
            )
            if len(c1m) < 300:
                ts += int(args.step_sec)
                continue
            entry = 0.0
            for c in reversed(c1m):
                if int(c["timestamp"]) <= ts:
                    entry = float(c["close"])
                    break
            if entry <= 0:
                ts += int(args.step_sec)
                continue

            entry_mode = (
                "continuation" if setup.checklist.get("continuation") else "retest"
            )
            ent, in_zone, zone_reason = propose_entry(setup, act, entry, entry_mode)
            if not in_zone:
                ts += int(args.step_sec)
                continue
            entry = ent

            sc = gate.score_at(
                db_path=db_path,
                symbol=sym,
                action=act,
                entry_price=entry,
                as_of_ts=ts,
                aggregator_confidence=0.62,
            )
            if (
                sc.phase != "ready"
                or sc.quality_score < ready_min
                or sc.win_probability < min_win
            ):
                ts += int(args.step_sec)
                continue

            last = last_alert.get(sym, 0)
            if ts - last < int(args.cooldown_sec):
                ts += int(args.step_sec)
                continue

            ltf = resample_ohlc(c1m, cfg.ltf_minutes * 60)
            htf = resample_ohlc(c1m, cfg.htf_minutes * 60)
            fl = finalize_structure_levels(
                setup,
                entry,
                act,
                min_rr=min_rr,
                min_sl_pct=min_sl,
                max_sl_pct=max_sl,
                ltf_swings=find_swings(ltf),
                htf_swings=find_swings(htf),
                book_zones=load_recent_book_zones_sync(db_path, sym),
                min_tp_pct=float(getattr(config.agent, "market_min_tp_room_pct", 1.2)),
                max_tp_pct=float(getattr(config.agent, "agg_tp_max_pct", 8.0)),
            )
            if not fl:
                ts += int(args.step_sec)
                continue

            path = [c for c in c1m if ts <= int(c["timestamp"]) <= ts + hold_sec]
            if len(path) < 15:
                ts += int(args.step_sec)
                continue

            sim = simulate_full_trade_exit(
                entry,
                fl.sl,
                fl.tp,
                act,
                path,
                partial_enabled=bool(getattr(config.agent, "paper_partial_enabled", True)),
                adaptive_partial=bool(getattr(config.agent, "paper_partial_adaptive", True)),
                partial_rr=float(getattr(config.agent, "paper_partial_rr", 1.5)),
                partial_size=float(getattr(config.agent, "paper_partial_size", 0.5)),
                be_after_partial=bool(getattr(config.agent, "paper_be_after_partial", True)),
            )
            if not sim:
                ts += int(args.step_sec)
                continue

            margin_pct = 0.10 if sc.win_probability >= 0.55 and sc.quality_score >= 76 else 0.03
            lev_pnl = sim.net_pnl_pct * args.leverage * margin_pct

            trades.append(
                {
                    "symbol": sym,
                    "action": act,
                    "ts": ts,
                    "quality": sc.quality_score,
                    "win_p": sc.win_probability,
                    "entry": entry,
                    "sl": fl.sl,
                    "tp": fl.tp,
                    "pnl_pct": sim.net_pnl_pct,
                    "reason": sim.exit_reason,
                    "partial": sim.partial_taken,
                    "lev_pnl_eur": lev_pnl * args.deposit / 100.0,
                }
            )
            last_alert[sym] = ts
            print(
                f"  {sym:12} {act:4} q={sc.quality_score:3} "
                f"pnl={sim.net_pnl_pct:+.2f}%({sim.exit_reason:7}) "
                f"partial={'Y' if sim.partial_taken else 'N'} "
                f"zone={entry_mode}"
            )
            ts += int(args.cooldown_sec)

    print(f"\n  scans={scanned}  trades={len(trades)}")
    if not trades:
        print("  No trades matched filters in this window.")
        print("=" * 72)
        return

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    reasons = Counter(t["reason"] for t in trades)
    sum_pct = sum(t["pnl_pct"] for t in trades)
    sum_eur = sum(t["lev_pnl_eur"] for t in trades)
    final_eur = args.deposit + sum_eur

    print(f"  WR: {100 * wins / len(trades):.1f}%  ({wins}/{len(trades)})")
    print(f"  Sum trade P/L: {sum_pct:+.2f}% (unlevered per trade)")
    print(
        f"  Portfolio (approx): {args.deposit:.2f} -> {final_eur:.2f} EUR "
        f"({100 * sum_eur / args.deposit:+.2f}%)"
    )
    print(f"  Exits: {dict(reasons)}")
    partial_n = sum(1 for t in trades if t["partial"])
    print(f"  Partial taken: {partial_n}/{len(trades)}")
    print("\n  Last 5 trades:")
    for t in trades[-5:]:
        print(
            f"    {t['symbol']:10} {t['action']:4} q={t['quality']} "
            f"{t['pnl_pct']:+.2f}% {t['reason']}"
        )
    print("=" * 72)
    print(
        "Note: replay uses zone + SMC levels + adaptive partial. "
        "Live flow/book gates are not in historical DB."
    )


if __name__ == "__main__":
    main()
