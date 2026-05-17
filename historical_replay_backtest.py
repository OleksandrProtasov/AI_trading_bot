"""Rebuild aggregator signals from historical raw events, then backtest."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.backtest_portfolio import backtest_config_from_app, run_aggregator_backtest
from core.btc_trend import BtcTrendCache
from core.entry_quality import passes_entry_quality
from core.edge_calibration import calibration_extra_bps, load_calibration
from core.ev_edge import evaluate_edge_gate
from core.runtime_paths import resolved_database_path


@dataclass
class RawSignal:
    ts: int
    agent_type: str
    symbol: str
    signal_type: str
    priority: str
    message: str
    data: Dict[str, Any]


def _priority_conf(priority: str) -> float:
    p = (priority or "medium").lower()
    return {
        "critical": 0.95,
        "urgent": 0.88,
        "high": 0.78,
        "medium": 0.58,
        "low": 0.38,
    }.get(p, 0.55)


def _signal_confidence(s: RawSignal) -> float:
    try:
        val = float((s.data or {}).get("confidence"))
        return max(0.0, min(1.0, val))
    except (TypeError, ValueError):
        return _priority_conf(s.priority)


def _classify_signal(s: RawSignal) -> str:
    st = (s.signal_type or "").lower()
    msg = (s.message or "").lower()
    data = s.data or {}

    if any(k in st for k in ("liquidity_crisis", "dump_danger", "rapid_dump")):
        return "exit"
    if any(k in st for k in ("exit", "danger", "crisis")):
        return "exit"
    if any(k in st for k in ("support_break", "sell", "dump")):
        return "sell"
    if any(k in st for k in ("resistance_break", "pump", "buy", "whale_activity")):
        return "buy"
    if "imbalance" in st:
        try:
            imb = float(data.get("imbalance", 0.0))
            if imb > 0:
                return "buy"
            if imb < 0:
                return "sell"
        except (TypeError, ValueError):
            pass
    if any(k in st for k in ("volume_spike", "price_spike", "high_volatility")):
        if any(k in msg for k in ("dump", "sell", "down", "bear")):
            return "sell"
        if any(k in msg for k in ("pump", "buy", "up", "bull")):
            return "buy"
    return "neutral"


def _group_score(items: List[RawSignal], weights: Dict[str, Dict[str, float]]) -> float:
    if not items:
        return 0.0
    weighted_conf_sum = 0.0
    total_weight = 0.0
    unique_agents = set()
    for s in items:
        type_w = float(weights.get(s.agent_type, {}).get(s.signal_type, 0.45))
        prio_w = _priority_conf(s.priority)
        weight = max(0.05, type_w * (0.5 + 0.5 * prio_w))
        weighted_conf_sum += weight * _signal_confidence(s)
        total_weight += weight
        unique_agents.add(s.agent_type)
    score = max(0.0, min(weighted_conf_sum / total_weight, 1.0)) if total_weight > 0 else 0.0
    if len(unique_agents) > 1:
        score = min(score + min((len(unique_agents) - 1) * 0.04, 0.16), 1.0)
    return score


def _extract_reasons(items: List[RawSignal]) -> List[str]:
    out: List[str] = []
    seen = set()
    for s in items:
        r = (s.data or {}).get("reason") or f"{s.agent_type}: {s.signal_type}"
        r = str(r)
        if r not in seen:
            seen.add(r)
            out.append(r)
        if len(out) >= 5:
            break
    return out


def _hours_ago_ts(hours: int) -> int:
    return int((datetime.utcnow() - timedelta(hours=hours)).timestamp())


def _copy_db(src: str, dst: str) -> None:
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _clear_aggregator_signals(conn: sqlite3.Connection, *, start_ts: int) -> None:
    conn.execute(
        "DELETE FROM signals WHERE agent_type = 'aggregator' AND timestamp >= ?",
        (start_ts,),
    )
    conn.commit()


def _load_raw(conn: sqlite3.Connection, *, start_ts: int, end_ts: Optional[int]) -> List[RawSignal]:
    params: List[Any] = [start_ts]
    q = """
        SELECT timestamp, agent_type, symbol, signal_type, priority, message, data
        FROM signals
        WHERE agent_type != 'aggregator'
          AND symbol IS NOT NULL
          AND symbol != ''
          AND timestamp >= ?
    """
    if end_ts is not None:
        q += " AND timestamp <= ?"
        params.append(end_ts)
    q += " ORDER BY timestamp ASC"
    rows = conn.execute(q, tuple(params)).fetchall()
    out: List[RawSignal] = []
    for ts, agent_type, symbol, signal_type, priority, message, data in rows:
        try:
            payload = json.loads(data) if data else {}
        except Exception:
            payload = {}
        out.append(
            RawSignal(
                ts=int(ts),
                agent_type=str(agent_type).lower(),
                symbol=str(symbol).upper(),
                signal_type=str(signal_type).lower(),
                priority=str(priority).lower(),
                message=str(message or ""),
                data=payload,
            )
        )
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Historical replay for aggregator logic")
    p.add_argument("--hours", type=int, default=24 * 30)
    p.add_argument("--end-ts", type=int, default=None)
    p.add_argument("--recent-window-sec", type=int, default=60)
    p.add_argument("--min-confidence", type=float, default=0.4)
    p.add_argument("--min-score", type=float, default=0.3)
    p.add_argument("--min-margin", type=float, default=0.1)
    p.add_argument("--dedup-sec", type=int, default=60)
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--fee-bps", type=float, default=2.0)
    p.add_argument("--max-open", type=int, default=3)
    p.add_argument("--entry-gap-sec", type=int, default=120)
    p.add_argument("--symbol-cooldown-sec", type=int, default=600)
    p.add_argument("--max-trades-per-symbol", type=int, default=4)
    p.add_argument("--slippage-bps", type=float, default=3.0)
    p.add_argument("--ev-buffer-bps", type=float, default=8.0)
    p.add_argument("--ev-confidence-mult", type=float, default=120.0)
    p.add_argument("--ev-margin-mult", type=float, default=80.0)
    p.add_argument("--ev-source-mult", type=float, default=3.0)
    p.add_argument("--ev-bearish-penalty-mult", type=float, default=6.0)
    p.add_argument("--ev-emergency-penalty-mult", type=float, default=4.0)
    p.add_argument("--ev-conflict-penalty-mult", type=float, default=25.0)
    args = p.parse_args()

    db_path = resolved_database_path()
    if args.end_ts is not None:
        start_ts = int(args.end_ts) - int(args.hours * 3600)
    else:
        start_ts = _hours_ago_ts(args.hours)

    # Keep weights aligned with runtime aggregator defaults.
    weights = {
        "emergency": {"price_spike": 1.0, "volume_spike": 0.8, "dump_danger": 1.0, "liquidity_crisis": 0.9},
        "market": {"resistance_break": 0.7, "support_break": 0.7, "volume_spike": 0.6, "high_volatility": 0.4},
        "onchain": {"whale_activity": 0.8, "whale_alert": 0.9},
        "liquidity": {"orderbook_imbalance": 0.6, "stop_cluster": 0.7, "liquidity_break": 0.8},
        "shitcoin": {"pump": 0.9, "dump": 1.0, "rapid_pump": 1.0, "rapid_dump": 1.0, "new_shitcoin": 0.3},
    }

    with tempfile.TemporaryDirectory() as tmp:
        replay_db = str(Path(tmp) / "replay.db")
        _copy_db(db_path, replay_db)
        conn = sqlite3.connect(replay_db)
        try:
            _clear_aggregator_signals(conn, start_ts=start_ts)
            raw = _load_raw(conn, start_ts=start_ts, end_ts=args.end_ts)
            by_symbol: Dict[str, List[RawSignal]] = defaultdict(list)
            last_sent_by_key: Dict[str, int] = {}
            inserted = 0
            ev_filtered = 0
            rr_filtered = 0
            btc_filtered = 0
            btc_cache = BtcTrendCache()
            rr_on = True
            min_profit_bps_default = 15.0
            cal_enabled = True
            edge_calibration = load_calibration()
            try:
                from config import config as app_config

                rr_on = bool(app_config.agent.agg_rr_gate_enabled)
                min_profit_bps_default = float(app_config.agent.agg_rr_min_profit_bps)
                cal_enabled = bool(app_config.agent.agg_edge_calibration_enabled)
            except Exception:
                pass

            for s in raw:
                bucket = by_symbol[s.symbol]
                bucket.append(s)
                min_ts = s.ts - args.recent_window_sec
                by_symbol[s.symbol] = [x for x in bucket if x.ts >= min_ts]
                window = by_symbol[s.symbol]

                buy = []
                sell = []
                exit_ = []
                bearish_pressure = 0
                emergency_count = 0
                for item in window:
                    cls = _classify_signal(item)
                    if cls == "buy":
                        buy.append(item)
                    elif cls == "sell":
                        sell.append(item)
                        if item.agent_type == "emergency":
                            exit_.append(item)
                    elif cls == "exit":
                        exit_.append(item)
                    if item.agent_type == "emergency":
                        emergency_count += 1
                    st = (item.signal_type or "").lower()
                    if any(k in st for k in ("dump", "danger", "crisis", "sell", "support_break", "exit")):
                        bearish_pressure += 1

                buy_s = _group_score(buy, weights)
                sell_s = _group_score(sell, weights)
                exit_s = _group_score(exit_, weights)
                max_s = max(buy_s, sell_s, exit_s)
                scores = sorted([buy_s, sell_s, exit_s], reverse=True)
                margin = scores[0] - scores[1]

                if max_s < args.min_score or margin < args.min_margin:
                    continue
                if exit_s >= max_s * 0.9:
                    action = "EXIT"
                    reasons = _extract_reasons(exit_)
                elif sell_s > buy_s:
                    action = "SELL"
                    reasons = _extract_reasons(sell)
                else:
                    action = "BUY"
                    reasons = _extract_reasons(buy)

                if max_s < args.min_confidence:
                    continue
                rr_on = True
                min_profit_bps = 15.0
                try:
                    from config import config as app_config

                    rr_on = bool(app_config.agent.agg_rr_gate_enabled)
                    min_profit_bps = float(app_config.agent.agg_rr_min_profit_bps)
                except Exception:
                    pass
                cal_extra, _ = calibration_extra_bps(
                    edge_calibration,
                    action=action,
                    confidence=max_s,
                    enabled=cal_enabled,
                )
                edge_res = evaluate_edge_gate(
                    action=action,
                    confidence=max_s,
                    margin=margin,
                    source_count=len(window),
                    bearish_pressure=bearish_pressure,
                    emergency_count=emergency_count,
                    buy_count=len(buy),
                    sell_count=len(sell),
                    fee_bps_per_side=float(args.fee_bps),
                    slippage_bps=float(args.slippage_bps),
                    buffer_bps=float(args.ev_buffer_bps),
                    min_profit_bps=min_profit_bps,
                    ev_gate_enabled=True,
                    rr_gate_enabled=rr_on,
                    calibration_extra_bps=cal_extra,
                    confidence_mult=float(args.ev_confidence_mult),
                    margin_mult=float(args.ev_margin_mult),
                    source_mult=float(args.ev_source_mult),
                    bearish_penalty_mult=float(args.ev_bearish_penalty_mult),
                    emergency_penalty_mult=float(args.ev_emergency_penalty_mult),
                    conflict_penalty_mult=float(args.ev_conflict_penalty_mult),
                )
                if not edge_res.passed:
                    ev_filtered += 1
                    if edge_res.min_profit_bps > 0:
                        rr_filtered += 1
                    continue

                try:
                    from config import config as app_config

                    min_unique = int(app_config.agent.agg_min_unique_agents)
                    min_dir_conf = float(app_config.agent.agg_directional_min_confidence)
                    require_quality = bool(
                        app_config.agent.agg_require_quality_agent_for_buy
                    )
                except Exception:
                    min_unique = 2
                    min_dir_conf = 0.58
                    require_quality = True

                bearish_on = True
                btc_on = True
                lookback_min = 30
                down_thr = -0.08
                up_thr = 0.08
                btc_sym = "BTCUSDT"
                try:
                    from config import config as app_config

                    bearish_on = bool(app_config.agent.agg_bearish_regime_enabled)
                    btc_on = bool(app_config.agent.agg_btc_trend_filter_enabled)
                    lookback_min = int(app_config.agent.agg_btc_trend_lookback_minutes)
                    down_thr = float(app_config.agent.agg_btc_trend_down_threshold_pct)
                    up_thr = float(app_config.agent.agg_btc_trend_up_threshold_pct)
                    btc_sym = str(app_config.agent.agg_btc_symbol)
                except Exception:
                    pass
                btc_snap = btc_cache.get(
                    replay_db,
                    s.ts,
                    lookback_minutes=lookback_min,
                    symbol=btc_sym,
                    down_threshold_pct=down_thr,
                    up_threshold_pct=up_thr,
                )
                ok, _ = passes_entry_quality(
                    action,
                    max_s,
                    buy,
                    sell,
                    min_unique_agents=min_unique,
                    min_directional_confidence=min_dir_conf,
                    require_quality_agent_for_buy=require_quality,
                    exit_signals=exit_,
                    emergency_count=emergency_count,
                    bearish_pressure=bearish_pressure,
                    buy_score=buy_s,
                    sell_score=sell_s,
                    bearish_regime_enabled=bearish_on,
                    symbol=s.symbol,
                    btc_trend=str(btc_snap.get("trend") or "unknown"),
                    btc_trend_return_pct=btc_snap.get("return_pct"),
                    btc_trend_filter_enabled=btc_on,
                )
                if not ok:
                    if action == "BUY" and str(btc_snap.get("trend")) == "down":
                        btc_filtered += 1
                    continue

                key = f"{s.symbol}:{action}"
                last_ts = last_sent_by_key.get(key, 0)
                if (s.ts - last_ts) < args.dedup_sec:
                    continue
                last_sent_by_key[key] = s.ts

                price = None
                for item in reversed(window):
                    try:
                        pval = float((item.data or {}).get("price"))
                        if pval > 0:
                            price = pval
                            break
                    except (TypeError, ValueError):
                        continue

                payload = {
                    "action": action,
                    "confidence": max_s,
                    "reasons": reasons,
                    "price": price,
                    "source_signals_count": len(window),
                }
                conn.execute(
                    """
                    INSERT INTO signals (timestamp, agent_type, symbol, signal_type, priority, message, data, sent_to_telegram)
                    VALUES (?, 'aggregator', ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        s.ts,
                        s.symbol,
                        action.lower(),
                        "high" if max_s >= 0.6 else "medium",
                        f"{action} replay signal for {s.symbol}",
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                inserted += 1

            conn.commit()

            cfg = backtest_config_from_app(
                min_confidence=args.min_confidence,
                horizon_minutes=args.horizon_minutes,
                fee_bps_per_side=args.fee_bps,
                max_open_positions=args.max_open,
                raw_include_stables=True,
                min_gap_between_entries_sec=args.entry_gap_sec,
                per_symbol_cooldown_sec=args.symbol_cooldown_sec,
                max_trades_per_symbol=args.max_trades_per_symbol,
            )
            res = run_aggregator_backtest(
                replay_db,
                start_ts=start_ts,
                end_ts=args.end_ts,
                cfg=cfg,
            )
            out = {
                "window_hours": args.hours,
                "replayed_aggregator_signals": inserted,
                "ev_filtered_signals": ev_filtered,
                "btc_trend_filtered_signals": btc_filtered,
                "rr_filtered_signals": rr_filtered,
                "ev_gate": {
                    "required_bps": 2.0 * float(args.fee_bps)
                    + float(args.slippage_bps)
                    + float(args.ev_buffer_bps)
                    + (min_profit_bps_default if rr_on else 0.0),
                    "min_profit_bps": min_profit_bps_default if rr_on else 0.0,
                    "slippage_bps": float(args.slippage_bps),
                    "buffer_bps": float(args.ev_buffer_bps),
                    "confidence_mult": float(args.ev_confidence_mult),
                    "margin_mult": float(args.ev_margin_mult),
                    "source_mult": float(args.ev_source_mult),
                    "bearish_penalty_mult": float(args.ev_bearish_penalty_mult),
                    "emergency_penalty_mult": float(args.ev_emergency_penalty_mult),
                    "conflict_penalty_mult": float(args.ev_conflict_penalty_mult),
                },
                "backtest": res,
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
        finally:
            conn.close()


if __name__ == "__main__":
    main()
