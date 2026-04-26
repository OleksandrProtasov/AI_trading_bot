"""Historical portfolio backtests: aggregator signals vs raw agent signals."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.utils import is_stable_coin


@dataclass
class BacktestConfig:
    min_confidence: float = 0.55
    horizon_minutes: int = 240
    fee_bps_per_side: float = 5.0
    max_open_positions: int = 1
    # If False, skip symbols treated as stables (USDT-only shitcoin rows are skipped).
    raw_include_stables: bool = False
    # If True and end_ts is not passed, evaluate only signals old enough to have horizon.
    only_matured_signals: bool = True
    # Strategy profile for backtest filtering.
    strategy_mode: str = "balanced"  # balanced | trend_following | defensive
    strategy_min_confidence: float = 0.55
    strategy_required_confirmations: int = 2
    strategy_bearish_guard_enabled: bool = True
    strategy_bearish_guard_threshold: int = 2
    # Portfolio allocator-lite constraints for backtest.
    min_gap_between_entries_sec: int = 0
    per_symbol_cooldown_sec: int = 0
    max_trades_per_symbol: int = 0  # 0 disables limit
    # Allocator-lite v2.
    emergency_trade_quota_per_hour: int = 0  # applies to raw mode
    loss_streak_for_cooloff: int = 0  # 0 disables
    cooloff_skipped_entries: int = 0
    # Allow exceptional setups to bypass cool-off.
    cooloff_override_confidence: float = 0.9
    cooloff_override_confirmations: int = 3


DEFAULT_RAW_AGENT_TYPES: Tuple[str, ...] = (
    "market",
    "liquidity",
    "onchain",
    "emergency",
    "shitcoin",
)


def _stable_coins() -> set:
    try:
        from config import config

        return set(config.stable_coins)
    except Exception:
        return {"USDT", "USDC", "BUSD", "DAI", "TUSD"}


def _priority_conf(priority: str) -> float:
    p = (priority or "medium").lower()
    return {
        "critical": 0.95,
        "urgent": 0.88,
        "high": 0.78,
        "medium": 0.58,
        "low": 0.38,
    }.get(p, 0.55)


def infer_side_from_raw_signal(signal_type: str) -> Optional[str]:
    """
    Map heuristic signal_type to BUY/SELL for offline PnL (aligned with aggregator keywords).
    """
    st = (signal_type or "").lower()
    if "rapid_dump" in st or ("dump" in st and "pump" not in st):
        return "SELL"
    if any(k in st for k in ("sell", "exit", "danger", "crisis", "liquidity_crisis")):
        return "SELL"
    if "support_break" in st:
        return "SELL"
    if any(
        k in st
        for k in (
            "pump",
            "buy",
            "break",
            "whale",
            "imbalance",
            "volume_spike",
            "price_spike",
            "resistance_break",
        )
    ):
        return "BUY"
    return None


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _entry_price_from_data(data: Dict[str, Any]) -> Optional[float]:
    for k in ("entry", "price"):
        p = _to_float(data.get(k))
        if p and p > 0:
            return p
    return None


def _close_price(
    cur: sqlite3.Cursor, symbol: str, ts: int, timeframe: str = "1m"
) -> Optional[float]:
    for sym in (symbol, symbol.upper(), symbol.lower()):
        cur.execute(
            """
            SELECT close FROM candles
            WHERE symbol = ? AND timeframe = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (sym, timeframe, ts),
        )
        row = cur.fetchone()
        if row:
            return _to_float(row[0])
    return None


def _effective_end_ts(
    cfg: BacktestConfig,
    *,
    end_ts: Optional[int],
) -> int:
    now_ts = int(datetime.utcnow().timestamp())
    if end_ts is not None:
        return end_ts
    if cfg.only_matured_signals:
        return now_ts - int(cfg.horizon_minutes * 60)
    return now_ts


def _strategy_allows_trade(
    action: str,
    confidence: float,
    data: Dict[str, Any],
    cfg: BacktestConfig,
) -> bool:
    """
    Backtest-side strategy gate mirroring runtime intent.
    Keeps this deterministic and independent from live agent objects.
    """
    mode = (cfg.strategy_mode or "balanced").lower()
    min_conf = max(cfg.min_confidence, cfg.strategy_min_confidence)
    reasons = data.get("reasons") or []
    reason_text = " | ".join(str(r).lower() for r in reasons)
    src_count = int(_to_float(data.get("source_signals_count")) or 0)
    bearish_guard = cfg.strategy_bearish_guard_enabled
    bearish_hits = sum(
        1
        for k in ("dump", "danger", "crisis", "sell", "support_break", "exit pressure")
        if k in reason_text
    )

    if confidence < min_conf:
        return False

    if action == "BUY" and bearish_guard and bearish_hits >= cfg.strategy_bearish_guard_threshold:
        return False

    if mode == "trend_following":
        # Require trend/momentum-like context for direction trades.
        if action in ("BUY", "SELL"):
            if src_count and src_count < max(1, cfg.strategy_required_confirmations):
                return False
            return any(k in reason_text for k in ("break", "trend", "momentum", "volume spike"))
        return False

    if mode == "defensive":
        # Demand higher confidence and avoid weak longs with visible dump/exit context.
        if confidence < (min_conf + 0.08):
            return False
        if src_count and src_count < max(2, cfg.strategy_required_confirmations + 1):
            return False
        if action == "BUY" and any(k in reason_text for k in ("dump", "exit", "danger", "crisis")):
            return False
        return action in ("BUY", "SELL")

    # balanced
    return action in ("BUY", "SELL")


def _cooloff_override_allowed(
    action: str,
    confidence: float,
    data: Dict[str, Any],
    cfg: BacktestConfig,
) -> bool:
    """
    Let only exceptional entries pass during cool-off.
    """
    if action not in ("BUY", "SELL"):
        return False
    src_count = int(_to_float(data.get("source_signals_count")) or 0)
    return (
        confidence >= max(cfg.min_confidence, cfg.cooloff_override_confidence)
        and src_count >= max(1, cfg.cooloff_override_confirmations)
    )


def run_aggregator_backtest(
    db_path: str,
    *,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    cfg: Optional[BacktestConfig] = None,
) -> Dict[str, Any]:
    cfg = cfg or BacktestConfig()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        end_ts = _effective_end_ts(cfg, end_ts=end_ts)
        where = ["agent_type = 'aggregator'"]
        params: List[Any] = []
        if start_ts is not None:
            where.append("timestamp >= ?")
            params.append(start_ts)
        where.append("timestamp <= ?")
        params.append(end_ts)
        q = (
            "SELECT timestamp, symbol, signal_type, data FROM signals WHERE "
            + " AND ".join(where)
            + " ORDER BY timestamp ASC"
        )
        cur.execute(q, tuple(params))
        rows = cur.fetchall()
        if not rows:
            return {"error": "No aggregator signals in selected window"}

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        trades: List[Dict[str, Any]] = []
        monthly: Dict[str, float] = {}
        open_slots: List[int] = []  # exit timestamps of currently open positions
        last_entry_ts = 0
        last_by_symbol: Dict[str, int] = {}
        trades_by_symbol: Dict[str, int] = {}
        loss_streak = 0
        cooloff_left = 0
        skipped = {
            "blocked_overlap": 0,
            "allocator_filtered": 0,
            "cooloff_filtered": 0,
            "cooloff_override_passed": 0,
            "non_trade_action": 0,
            "low_confidence": 0,
            "strategy_filtered": 0,
            "missing_symbol": 0,
            "missing_entry_price": 0,
            "out_of_range_exit": 0,
            "missing_exit_price": 0,
        }

        for row in rows:
            ts = int(row["timestamp"])
            data = json.loads(row["data"]) if row["data"] else {}
            action = str(data.get("action") or row["signal_type"]).upper()
            conf = _to_float(data.get("confidence")) or 0.0
            if cooloff_left > 0:
                if _cooloff_override_allowed(action, conf, data, cfg):
                    skipped["cooloff_override_passed"] += 1
                else:
                    cooloff_left -= 1
                    skipped["cooloff_filtered"] += 1
                    continue
            open_slots = [et for et in open_slots if et > ts]
            if len(open_slots) >= max(1, int(cfg.max_open_positions)):
                skipped["blocked_overlap"] += 1
                continue
            if (
                cfg.min_gap_between_entries_sec > 0
                and (ts - last_entry_ts) < cfg.min_gap_between_entries_sec
            ):
                skipped["allocator_filtered"] += 1
                continue
            if action not in ("BUY", "SELL"):
                skipped["non_trade_action"] += 1
                continue
            if conf < cfg.min_confidence:
                skipped["low_confidence"] += 1
                continue
            if not _strategy_allows_trade(action, conf, data, cfg):
                skipped["strategy_filtered"] += 1
                continue

            symbol = (row["symbol"] or "").strip()
            if not symbol:
                skipped["missing_symbol"] += 1
                continue
            if (
                cfg.per_symbol_cooldown_sec > 0
                and symbol in last_by_symbol
                and (ts - last_by_symbol[symbol]) < cfg.per_symbol_cooldown_sec
            ):
                skipped["allocator_filtered"] += 1
                continue
            if cfg.max_trades_per_symbol > 0 and trades_by_symbol.get(symbol, 0) >= cfg.max_trades_per_symbol:
                skipped["allocator_filtered"] += 1
                continue
            entry_price = _entry_price_from_data(data)
            if entry_price is None:
                entry_price = _close_price(cur, symbol, ts)
            if entry_price is None or entry_price <= 0:
                skipped["missing_entry_price"] += 1
                continue

            exit_ts = ts + cfg.horizon_minutes * 60
            if exit_ts > end_ts:
                skipped["out_of_range_exit"] += 1
                continue
            exit_price = _close_price(cur, symbol, exit_ts)
            if exit_price is None or exit_price <= 0:
                skipped["missing_exit_price"] += 1
                continue

            open_slots.append(exit_ts)
            last_entry_ts = ts
            last_by_symbol[symbol] = ts
            trades_by_symbol[symbol] = trades_by_symbol.get(symbol, 0) + 1

            gross_ret = (exit_price - entry_price) / entry_price
            if action == "SELL":
                gross_ret = -gross_ret
            fee = 2.0 * (cfg.fee_bps_per_side / 10000.0)
            net_ret = gross_ret - fee
            equity *= 1.0 + net_ret
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

            month_key = datetime.utcfromtimestamp(ts).strftime("%Y-%m")
            monthly[month_key] = monthly.get(month_key, 0.0) + net_ret * 100.0

            trades.append(
                {
                    "symbol": symbol,
                    "action": action,
                    "ts": ts,
                    "exit_ts": exit_ts,
                    "confidence": conf,
                    "entry": entry_price,
                    "exit": exit_price,
                    "net_return_pct": net_ret * 100.0,
                }
            )
            if net_ret < 0:
                loss_streak += 1
            else:
                loss_streak = 0
            if (
                cfg.loss_streak_for_cooloff > 0
                and cfg.cooloff_skipped_entries > 0
                and loss_streak >= cfg.loss_streak_for_cooloff
            ):
                cooloff_left = cfg.cooloff_skipped_entries
                loss_streak = 0
        wins = sum(1 for t in trades if t["net_return_pct"] > 0)
        return {
            "mode": "aggregator",
            "config": {
                "min_confidence": cfg.min_confidence,
                "horizon_minutes": cfg.horizon_minutes,
                "fee_bps_per_side": cfg.fee_bps_per_side,
                "max_open_positions": cfg.max_open_positions,
                "strategy_mode": cfg.strategy_mode,
                "strategy_min_confidence": cfg.strategy_min_confidence,
                "strategy_required_confirmations": cfg.strategy_required_confirmations,
                "min_gap_between_entries_sec": cfg.min_gap_between_entries_sec,
                "per_symbol_cooldown_sec": cfg.per_symbol_cooldown_sec,
                "max_trades_per_symbol": cfg.max_trades_per_symbol,
                "emergency_trade_quota_per_hour": cfg.emergency_trade_quota_per_hour,
                "loss_streak_for_cooloff": cfg.loss_streak_for_cooloff,
                "cooloff_skipped_entries": cfg.cooloff_skipped_entries,
                "cooloff_override_confidence": cfg.cooloff_override_confidence,
                "cooloff_override_confirmations": cfg.cooloff_override_confirmations,
            },
            "signals_seen": len(rows),
            "trades": len(trades),
            "win_rate_pct": (wins / len(trades) * 100.0) if trades else 0.0,
            "final_equity": equity,
            "total_return_pct": (equity - 1.0) * 100.0,
            "max_drawdown_pct": max_dd * 100.0,
            "monthly_return_pct": monthly,
            "last_trades": trades[-10:],
            "skipped": skipped,
        }
    finally:
        conn.close()


def run_raw_signals_backtest(
    db_path: str,
    *,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    cfg: Optional[BacktestConfig] = None,
    agent_types: Sequence[str] = DEFAULT_RAW_AGENT_TYPES,
) -> Dict[str, Any]:
    """Backtest using stored non-aggregator signals (heuristic BUY/SELL from signal_type)."""
    cfg = cfg or BacktestConfig()
    stables = _stable_coins()
    agents = tuple(a.strip().lower() for a in agent_types if a.strip())
    if not agents:
        return {"error": "No agent_types provided"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        end_ts = _effective_end_ts(cfg, end_ts=end_ts)
        ph = ",".join("?" * len(agents))
        where = [
            f"agent_type IN ({ph})",
            "agent_type != 'aggregator'",
            "symbol IS NOT NULL",
            "symbol != ''",
        ]
        params: List[Any] = list(agents)
        if start_ts is not None:
            where.append("timestamp >= ?")
            params.append(start_ts)
        where.append("timestamp <= ?")
        params.append(end_ts)
        q = (
            "SELECT timestamp, symbol, signal_type, agent_type, priority, data FROM signals WHERE "
            + " AND ".join(where)
            + " ORDER BY timestamp ASC"
        )
        cur.execute(q, tuple(params))
        rows = cur.fetchall()
        if not rows:
            return {"error": "No raw signals in selected window", "agent_types": list(agents)}

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        trades: List[Dict[str, Any]] = []
        monthly: Dict[str, float] = {}
        open_slots: List[int] = []  # exit timestamps of currently open positions
        last_entry_ts = 0
        last_by_symbol: Dict[str, int] = {}
        trades_by_symbol: Dict[str, int] = {}
        emergency_trades_by_hour: Dict[int, int] = {}
        loss_streak = 0
        cooloff_left = 0
        skipped = {
            "blocked_overlap": 0,
            "allocator_filtered": 0,
            "emergency_quota_filtered": 0,
            "cooloff_filtered": 0,
            "cooloff_override_passed": 0,
            "stable_skipped": 0,
            "no_side": 0,
            "low_confidence": 0,
            "strategy_filtered": 0,
            "missing_entry_price": 0,
            "out_of_range_exit": 0,
            "missing_exit_price": 0,
        }

        for row in rows:
            ts = int(row["timestamp"])
            data = json.loads(row["data"]) if row["data"] else {}
            action = infer_side_from_raw_signal(row["signal_type"])
            conf = _to_float(data.get("confidence"))
            if conf is None:
                conf = _priority_conf(row["priority"])
            if cooloff_left > 0:
                if _cooloff_override_allowed(action or "", conf, data, cfg):
                    skipped["cooloff_override_passed"] += 1
                else:
                    cooloff_left -= 1
                    skipped["cooloff_filtered"] += 1
                    continue
            open_slots = [et for et in open_slots if et > ts]
            if len(open_slots) >= max(1, int(cfg.max_open_positions)):
                skipped["blocked_overlap"] += 1
                continue
            if (
                cfg.min_gap_between_entries_sec > 0
                and (ts - last_entry_ts) < cfg.min_gap_between_entries_sec
            ):
                skipped["allocator_filtered"] += 1
                continue

            symbol = (row["symbol"] or "").strip()
            if not symbol:
                skipped["stable_skipped"] += 1
                continue
            if (
                cfg.per_symbol_cooldown_sec > 0
                and symbol in last_by_symbol
                and (ts - last_by_symbol[symbol]) < cfg.per_symbol_cooldown_sec
            ):
                skipped["allocator_filtered"] += 1
                continue
            if cfg.max_trades_per_symbol > 0 and trades_by_symbol.get(symbol, 0) >= cfg.max_trades_per_symbol:
                skipped["allocator_filtered"] += 1
                continue
            if not cfg.raw_include_stables and is_stable_coin(symbol, stables):
                skipped["stable_skipped"] += 1
                continue

            if action is None:
                skipped["no_side"] += 1
                continue

            if conf < cfg.min_confidence:
                skipped["low_confidence"] += 1
                continue
            if not _strategy_allows_trade(action, conf, data, cfg):
                skipped["strategy_filtered"] += 1
                continue
            if row["agent_type"] == "emergency" and cfg.emergency_trade_quota_per_hour > 0:
                bucket = ts // 3600
                used = emergency_trades_by_hour.get(bucket, 0)
                if used >= cfg.emergency_trade_quota_per_hour:
                    skipped["emergency_quota_filtered"] += 1
                    continue

            entry_price = _entry_price_from_data(data)
            if entry_price is None:
                entry_price = _close_price(cur, symbol, ts)
            if entry_price is None or entry_price <= 0:
                skipped["missing_entry_price"] += 1
                continue

            exit_ts = ts + cfg.horizon_minutes * 60
            if exit_ts > end_ts:
                skipped["out_of_range_exit"] += 1
                continue
            exit_price = _close_price(cur, symbol, exit_ts)
            if exit_price is None or exit_price <= 0:
                skipped["missing_exit_price"] += 1
                continue

            open_slots.append(exit_ts)
            last_entry_ts = ts
            last_by_symbol[symbol] = ts
            trades_by_symbol[symbol] = trades_by_symbol.get(symbol, 0) + 1
            if row["agent_type"] == "emergency" and cfg.emergency_trade_quota_per_hour > 0:
                bucket = ts // 3600
                emergency_trades_by_hour[bucket] = emergency_trades_by_hour.get(bucket, 0) + 1

            gross_ret = (exit_price - entry_price) / entry_price
            if action == "SELL":
                gross_ret = -gross_ret
            fee = 2.0 * (cfg.fee_bps_per_side / 10000.0)
            net_ret = gross_ret - fee
            equity *= 1.0 + net_ret
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

            month_key = datetime.utcfromtimestamp(ts).strftime("%Y-%m")
            monthly[month_key] = monthly.get(month_key, 0.0) + net_ret * 100.0

            trades.append(
                {
                    "symbol": symbol,
                    "action": action,
                    "agent": row["agent_type"],
                    "signal_type": row["signal_type"],
                    "ts": ts,
                    "exit_ts": exit_ts,
                    "confidence": conf,
                    "entry": entry_price,
                    "exit": exit_price,
                    "net_return_pct": net_ret * 100.0,
                }
            )
            if net_ret < 0:
                loss_streak += 1
            else:
                loss_streak = 0
            if (
                cfg.loss_streak_for_cooloff > 0
                and cfg.cooloff_skipped_entries > 0
                and loss_streak >= cfg.loss_streak_for_cooloff
            ):
                cooloff_left = cfg.cooloff_skipped_entries
                loss_streak = 0

        wins = sum(1 for t in trades if t["net_return_pct"] > 0)
        return {
            "mode": "raw",
            "agent_types": list(agents),
            "config": {
                "min_confidence": cfg.min_confidence,
                "horizon_minutes": cfg.horizon_minutes,
                "fee_bps_per_side": cfg.fee_bps_per_side,
                "max_open_positions": cfg.max_open_positions,
                "raw_include_stables": cfg.raw_include_stables,
                "strategy_mode": cfg.strategy_mode,
                "strategy_min_confidence": cfg.strategy_min_confidence,
                "strategy_required_confirmations": cfg.strategy_required_confirmations,
                "min_gap_between_entries_sec": cfg.min_gap_between_entries_sec,
                "per_symbol_cooldown_sec": cfg.per_symbol_cooldown_sec,
                "max_trades_per_symbol": cfg.max_trades_per_symbol,
                "emergency_trade_quota_per_hour": cfg.emergency_trade_quota_per_hour,
                "loss_streak_for_cooloff": cfg.loss_streak_for_cooloff,
                "cooloff_skipped_entries": cfg.cooloff_skipped_entries,
                "cooloff_override_confidence": cfg.cooloff_override_confidence,
                "cooloff_override_confirmations": cfg.cooloff_override_confirmations,
            },
            "signals_seen": len(rows),
            "trades": len(trades),
            "win_rate_pct": (wins / len(trades) * 100.0) if trades else 0.0,
            "final_equity": equity,
            "total_return_pct": (equity - 1.0) * 100.0,
            "max_drawdown_pct": max_dd * 100.0,
            "monthly_return_pct": monthly,
            "last_trades": trades[-10:],
            "skipped": skipped,
        }
    finally:
        conn.close()


def run_backtest_compare(
    db_path: str,
    *,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    cfg: Optional[BacktestConfig] = None,
    raw_agent_types: Sequence[str] = DEFAULT_RAW_AGENT_TYPES,
) -> Dict[str, Any]:
    """Run aggregator and raw backtests with the same capital rules."""
    cfg = cfg or BacktestConfig()
    agg = run_aggregator_backtest(db_path, start_ts=start_ts, end_ts=end_ts, cfg=cfg)
    raw = run_raw_signals_backtest(
        db_path, start_ts=start_ts, end_ts=end_ts, cfg=cfg, agent_types=raw_agent_types
    )
    note = (
        "Heuristic raw mapping from signal_type; use as baseline only. "
        "10%/month is not guaranteed - tune signals, fees, and horizon on your data."
    )
    return {"note": note, "aggregator": agg, "raw": raw}
