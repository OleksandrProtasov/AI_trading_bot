"""Compare baseline vs partial TP on paper trades and replay signals."""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.candle_resample import load_candles_1m_sync
from core.partial_exit import simulate_full_trade_exit


def _load_paper_trades(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM paper_trades ORDER BY opened_at ASC"
            ).fetchall()
        ]
    finally:
        conn.close()


def _candles_for_trade(db_path: str, trade: dict, hold_hours: float) -> list:
    sym = trade["symbol"]
    opened = int(trade["opened_at"])
    end_ts = opened + int(hold_hours * 3600)
    c1m = load_candles_1m_sync(db_path, sym, limit=50000, end_ts=end_ts)
    return [c for c in c1m if int(c["timestamp"]) >= (opened // 60) * 60]


def _summarize(label: str, rows: list) -> None:
    if not rows:
        print(f"\n  [{label}] no trades")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    tp = sum(1 for r in rows if r["reason"] == "tp")
    partial = sum(1 for r in rows if r.get("partial_taken"))
    sl = sum(1 for r in rows if r["reason"] == "sl")
    to = sum(1 for r in rows if r["reason"] == "timeout")
    s = sum(r["pnl"] for r in rows)
    print(f"\n  [{label}]")
    print(f"  trades={n} WR={100*wins/n:.1f}% sum={s:+.2f}% avg={s/n:+.2f}%")
    print(f"  tp={tp} partial_taken={partial} sl={sl} timeout={to}")
    improved = sum(
        1 for r in rows if r.get("delta", 0) > 0.01
    )
    worse = sum(1 for r in rows if r.get("delta", 0) < -0.01)
    print(f"  vs baseline: improved={improved} worse={worse} unchanged={n-improved-worse}")


def run_paper_compare(db_path: str, hold_hours: float) -> None:
    trades = _load_paper_trades(db_path)
    if not trades:
        print("No paper trades in DB")
        return

    base_rows: list = []
    part_rows: list = []
    adapt_rows: list = []
    details: list = []

    print("=" * 72)
    print(f"PAPER TRADES | hold={hold_hours}h | baseline vs partial @ 1.5R (50%)")
    print("=" * 72)

    for t in trades:
        candles = _candles_for_trade(db_path, t, hold_hours)
        if len(candles) < 5:
            continue
        entry = float(t["entry"])
        sl = float(t["sl"])
        tp = float(t["tp"])
        act = t["action"]

        base = simulate_full_trade_exit(
            entry, sl, tp, act, candles, partial_enabled=False
        )
        part = simulate_full_trade_exit(
            entry, sl, tp, act, candles, partial_enabled=True
        )
        adapt = simulate_full_trade_exit(
            entry,
            sl,
            tp,
            act,
            candles,
            partial_enabled=True,
            adaptive_partial=True,
            adaptive_min_tp_pct=3.5,
            adaptive_min_rr=2.8,
        )
        if not base or not part or not adapt:
            continue

        actual = float(t["pnl_pct"]) if t.get("pnl_pct") is not None else None
        row = {
            "id": t["id"],
            "symbol": t["symbol"],
            "action": act,
            "base_pnl": base.net_pnl_pct,
            "part_pnl": part.net_pnl_pct,
            "adapt_pnl": adapt.net_pnl_pct,
            "delta": part.net_pnl_pct - base.net_pnl_pct,
            "adapt_delta": adapt.net_pnl_pct - base.net_pnl_pct,
            "base_reason": base.exit_reason,
            "part_reason": part.exit_reason,
            "adapt_reason": adapt.exit_reason,
            "partial_taken": part.partial_taken,
            "adapt_partial": adapt.partial_taken,
            "actual_pnl": actual,
            "actual_reason": t.get("exit_reason"),
        }
        details.append(row)
        base_rows.append({"pnl": base.net_pnl_pct, "reason": base.exit_reason})
        part_rows.append(
            {
                "pnl": part.net_pnl_pct,
                "reason": part.exit_reason,
                "partial_taken": part.partial_taken,
                "delta": row["delta"],
            }
        )
        adapt_rows.append(
            {
                "pnl": adapt.net_pnl_pct,
                "reason": adapt.exit_reason,
                "partial_taken": adapt.partial_taken,
                "delta": row["adapt_delta"],
            }
        )

    _summarize("baseline (full TP only)", base_rows)
    _summarize("partial always @ 1.5R", part_rows)
    _summarize("partial adaptive (tp>=3.5% or rr>=2.8)", adapt_rows)

    print("\n  Per-symbol delta (partial - baseline):")
    by_sym: dict = {}
    for d in details:
        by_sym.setdefault(d["symbol"], []).append(d["delta"])
    for sym, deltas in sorted(by_sym.items(), key=lambda x: -sum(x[1])):
        print(f"    {sym:12} n={len(deltas):2}  sum_delta={sum(deltas):+.2f}%")

    print("\n  Best improvements:")
    for d in sorted(details, key=lambda x: -x["delta"])[:8]:
        print(
            f"    #{d['id']} {d['symbol']:10} {d['action']:4} "
            f"{d['base_pnl']:+.2f}% -> {d['part_pnl']:+.2f}% ({d['delta']:+.2f}%) "
            f"{d['base_reason']} -> {d['part_reason']}"
            + (" +partial" if d["partial_taken"] else "")
        )

    print("\n  Regressions:")
    for d in sorted(details, key=lambda x: x["delta"])[:5]:
        if d["delta"] >= -0.01:
            continue
        print(
            f"    #{d['id']} {d['symbol']:10} {d['base_pnl']:+.2f}% -> "
            f"{d['part_pnl']:+.2f}% ({d['delta']:+.2f}%)"
        )

    closed = [d for d in details if d.get("actual_pnl") is not None]
    if closed:
        act_sum = sum(d["actual_pnl"] for d in closed)
        adapt_sum = sum(d["adapt_pnl"] for d in closed)
        print(f"\n  Actual live paper sum={act_sum:+.2f}% | adaptive replay={adapt_sum:+.2f}%")
    print("=" * 72)


def run_replay_symbols(db_path: str, hours: int, hold_hours: float) -> None:
    """Scan historical ready setups across symbols (diverse charts)."""
    from config import config
    from core.smc_analysis import find_swings
    from core.candle_resample import resample_ohlc
    from core.structure_gate import StructureGate
    from core.structure_levels import finalize_structure_levels
    from core.liquidity_targets import load_recent_book_zones_sync

    gate = StructureGate()
    cfg = gate._config_from_agent()
    min_sl = float(getattr(config.agent, "agg_structure_min_sl_pct", 0.55))
    max_sl = float(getattr(config.agent, "agg_structure_max_sl_pct", 2.5))
    min_rr = float(getattr(config.agent, "agg_structure_min_rr", 2.5))
    ready_min = int(getattr(config.agent, "analyst_ready_min_score", 76))

    conn = sqlite3.connect(db_path)
    syms = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT symbol FROM candles
            WHERE timeframe='1m' AND timestamp >= ?
            GROUP BY symbol
            HAVING COUNT(*) > 500
            ORDER BY COUNT(*) DESC
            LIMIT 60
            """,
            (int(time.time()) - hours * 3600,),
        ).fetchall()
    ]
    conn.close()

    base_rows: list = []
    part_rows: list = []
    adapt_rows: list = []
    tested = 0
    since = int(time.time()) - hours * 3600

    print("=" * 72)
    print(f"HISTORICAL SCAN | last {hours}h | {len(syms)} symbols | hold={hold_hours}h")
    print("=" * 72)

    step = max(3600, int(hours * 3600 / 48))
    for sym in syms:
        sym = str(sym).upper()
        for ts in range(since + 3600, int(time.time()) - int(hold_hours * 3600), step):
            setup = gate.scan_symbol(db_path, sym, as_of_ts=ts)
            if not setup or setup.state != "ready":
                continue
            act = "BUY" if setup.side == "long" else "SELL"
            c1m = load_candles_1m_sync(db_path, sym, limit=8000, end_ts=ts + int(hold_hours * 3600))
            if len(c1m) < 300:
                continue
            entry = float(c1m[-1]["close"]) if c1m else 0
            for c in reversed(c1m):
                if int(c["timestamp"]) <= ts:
                    entry = float(c["close"])
                    break
            if entry <= 0:
                continue
            sc = gate.score_at(
                db_path=db_path,
                symbol=sym,
                action=act,
                entry_price=entry,
                as_of_ts=ts,
                aggregator_confidence=0.6,
            )
            if sc.phase != "ready" or sc.quality_score < ready_min:
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
            )
            if not fl:
                continue
            path = [c for c in c1m if ts <= int(c["timestamp"]) <= ts + int(hold_hours * 3600)]
            if len(path) < 15:
                continue
            base = simulate_full_trade_exit(
                entry, fl.sl, fl.tp, act, path, partial_enabled=False
            )
            part = simulate_full_trade_exit(
                entry, fl.sl, fl.tp, act, path, partial_enabled=True, adaptive_partial=False
            )
            adapt = simulate_full_trade_exit(
                entry,
                fl.sl,
                fl.tp,
                act,
                path,
                partial_enabled=True,
                adaptive_partial=True,
            )
            if not base or not part or not adapt:
                continue
            tested += 1
            base_rows.append({"pnl": base.net_pnl_pct, "reason": base.exit_reason})
            part_rows.append(
                {
                    "pnl": part.net_pnl_pct,
                    "reason": part.exit_reason,
                    "partial_taken": part.partial_taken,
                    "delta": part.net_pnl_pct - base.net_pnl_pct,
                }
            )
            adapt_rows.append(
                {
                    "pnl": adapt.net_pnl_pct,
                    "reason": adapt.exit_reason,
                    "partial_taken": adapt.partial_taken,
                    "delta": adapt.net_pnl_pct - base.net_pnl_pct,
                }
            )
            print(
                f"  {sym:12} {act:4} q={sc.quality_score:3} "
                f"base={base.net_pnl_pct:+.2f}%({base.exit_reason:7}) "
                f"adapt={adapt.net_pnl_pct:+.2f}%({adapt.exit_reason:7}) "
                f"d={adapt.net_pnl_pct - base.net_pnl_pct:+.2f}%"
                + (" [partial]" if adapt.partial_taken else " [full]")
            )
            break
        if tested >= 35:
            break

    print(f"\n  historical setups simulated: {tested}")
    _summarize("baseline", base_rows)
    _summarize("partial always", part_rows)
    _summarize("partial adaptive", adapt_rows)
    print("=" * 72)


def main() -> None:
    p = argparse.ArgumentParser(description="Compare partial vs baseline TP on history")
    p.add_argument("--db", default=str(ROOT / "crypto_analytics.db"))
    p.add_argument("--hold-hours", type=float, default=24.0)
    p.add_argument("--replay-hours", type=int, default=0, help="Also scan replay N hours")
    args = p.parse_args()

    run_paper_compare(args.db, args.hold_hours)
    if args.replay_hours > 0:
        run_replay_symbols(args.db, args.replay_hours, args.hold_hours)


if __name__ == "__main__":
    main()
