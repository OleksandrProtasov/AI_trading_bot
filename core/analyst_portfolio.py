"""Leveraged EUR portfolio simulation for analyst-mode backtests."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.backtest_portfolio import (
    BacktestConfig,
    _candles_range,
    _close_price,
    _cooloff_override_allowed,
    _effective_end_ts,
    _entry_price_from_data,
    _strategy_allows_trade,
    _to_float,
    backtest_config_from_app,
)
from core.structure_gate import StructureGate
from core.trade_levels import (
    levels_from_data_or_compute,
    passes_min_rr,
    simulate_sl_tp_path,
)


def position_size_from_confidence(confidence: float) -> float:
    """Fallback sizing from aggregator confidence when SMC score is low."""
    c = float(confidence or 0.0)
    if c >= 0.78:
        return 0.10
    if c >= 0.58:
        t = max(0.0, min(1.0, (c - 0.58) / 0.20))
        return 0.01 + t * 0.04
    return 0.0


def position_size_pct(*, quality: int, win_prob: float) -> float:
    """
    Margin fraction of equity per trade.
    High conviction → 10%; otherwise 1–5% scaled by win probability.
    """
    q = int(quality or 0)
    w = float(win_prob or 0.0)
    if q < 72 or w < 0.45:
        return 0.0
    if w >= 0.55 and q >= 76:
        return 0.10
    # 1% at 45% win → 5% at 55%+
    t = max(0.0, min(1.0, (w - 0.45) / 0.10))
    return 0.01 + t * 0.04


@dataclass
class AnalystPortfolioConfig:
    starting_eur: float = 100.0
    leverage: float = 10.0
    ready_min_score: int = 72
    min_win_probability: float = 0.45
    allow_forming: bool = False
    forming_max_position_pct: float = 0.02
    score_at_runtime: bool = False
    use_confidence_sizing: bool = False
    horizon_minutes: int = 30
    fee_bps_per_side: float = 2.0
    max_open_positions: int = 3
    sl_pct: float = 0.35
    tp_rr_ratio: float = 3.0


def run_analyst_portfolio_backtest(
    db_path: str,
    *,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    portfolio: Optional[AnalystPortfolioConfig] = None,
    bt_cfg: Optional[BacktestConfig] = None,
) -> Dict[str, Any]:
    pf = portfolio or AnalystPortfolioConfig()
    cfg = bt_cfg or backtest_config_from_app(horizon_minutes=pf.horizon_minutes)

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
        cur.execute(
            "SELECT timestamp, symbol, signal_type, data FROM signals WHERE "
            + " AND ".join(where)
            + " ORDER BY timestamp ASC",
            tuple(params),
        )
        rows = cur.fetchall()
        if not rows:
            return {"error": "No aggregator signals in window"}

        equity = float(pf.starting_eur)
        peak = equity
        max_dd = 0.0
        trades: List[Dict[str, Any]] = []
        open_slots: List[int] = []
        last_entry_ts = 0
        last_by_symbol: Dict[str, int] = {}
        trades_by_symbol: Dict[str, int] = {}
        loss_streak = 0
        cooloff_left = 0
        skipped: Dict[str, int] = {
            "overlap": 0,
            "low_quality": 0,
            "non_trade": 0,
            "no_entry": 0,
            "no_exit": 0,
            "low_confidence": 0,
            "strategy_filtered": 0,
            "allocator_filtered": 0,
            "cooloff_filtered": 0,
        }
        gate = StructureGate() if pf.score_at_runtime else None

        for row in rows:
            ts = int(row["timestamp"])
            data = json.loads(row["data"]) if row["data"] else {}
            action = str(data.get("action") or row["signal_type"]).upper()
            conf = float(_to_float(data.get("confidence")) or 0.0)

            if cooloff_left > 0:
                if not _cooloff_override_allowed(action, conf, data, cfg):
                    cooloff_left -= 1
                    skipped["cooloff_filtered"] += 1
                    continue

            if action not in ("BUY", "SELL"):
                skipped["non_trade"] += 1
                continue

            open_slots = [et for et in open_slots if et > ts]
            if len(open_slots) >= max(1, int(cfg.max_open_positions)):
                skipped["overlap"] += 1
                continue
            if (
                cfg.min_gap_between_entries_sec > 0
                and (ts - last_entry_ts) < cfg.min_gap_between_entries_sec
            ):
                skipped["allocator_filtered"] += 1
                continue
            if conf < cfg.min_confidence:
                skipped["low_confidence"] += 1
                continue
            if not _strategy_allows_trade(action, conf, data, cfg):
                skipped["strategy_filtered"] += 1
                continue

            symbol = (row["symbol"] or "").strip()
            if not symbol:
                skipped["no_entry"] += 1
                continue
            if (
                cfg.per_symbol_cooldown_sec > 0
                and symbol in last_by_symbol
                and (ts - last_by_symbol[symbol]) < cfg.per_symbol_cooldown_sec
            ):
                skipped["allocator_filtered"] += 1
                continue
            if (
                cfg.max_trades_per_symbol > 0
                and trades_by_symbol.get(symbol, 0) >= cfg.max_trades_per_symbol
            ):
                skipped["allocator_filtered"] += 1
                continue

            quality = int(_to_float(data.get("setup_quality")) or 0)
            win_prob = float(_to_float(data.get("win_probability")) or 0.0)
            phase = str(data.get("setup_phase") or "")

            entry_price = _entry_price_from_data(data)
            if entry_price is None:
                entry_price = _close_price(cur, symbol, ts)

            if pf.score_at_runtime and gate and entry_price:
                conf = float(_to_float(data.get("confidence")) or 0.0)
                sc = gate.score_at(
                    db_path=db_path,
                    symbol=symbol,
                    action=action,
                    entry_price=entry_price,
                    as_of_ts=ts,
                    aggregator_confidence=conf,
                )
                quality = sc.quality_score
                win_prob = sc.win_probability
                phase = sc.phase
                if sc.sl and sc.tp:
                    data = {**data, "sl": sc.sl, "tp": sc.tp}

            if pf.use_confidence_sizing:
                conf = float(_to_float(data.get("confidence")) or 0.0)
                pos_pct = position_size_from_confidence(conf)
                if pos_pct <= 0:
                    skipped["low_quality"] += 1
                    continue
                quality = int(conf * 100)
                win_prob = conf
                phase = "ready"
            else:
                if phase != "ready" and not (pf.allow_forming and phase == "forming"):
                    skipped["low_quality"] += 1
                    continue
                if quality < pf.ready_min_score or win_prob < pf.min_win_probability:
                    skipped["low_quality"] += 1
                    continue

                if phase == "forming" and pf.allow_forming:
                    pos_pct = min(
                        pf.forming_max_position_pct,
                        position_size_pct(quality=quality, win_prob=win_prob)
                        or pf.forming_max_position_pct,
                    )
                else:
                    pos_pct = position_size_pct(quality=quality, win_prob=win_prob)
                if pos_pct <= 0:
                    skipped["low_quality"] += 1
                    continue

            if entry_price is None or entry_price <= 0:
                skipped["no_entry"] += 1
                continue

            exit_ts = ts + pf.horizon_minutes * 60
            if exit_ts > end_ts:
                skipped["no_exit"] += 1
                continue

            levels = levels_from_data_or_compute(
                entry_price,
                action,
                data,
                sl_pct=pf.sl_pct,
                tp_rr_ratio=pf.tp_rr_ratio,
            )
            if not passes_min_rr(
                levels.sl_pct, levels.tp_pct, min_rr=cfg.min_rr_ratio
            ):
                skipped["strategy_filtered"] += 1
                continue
            net_ret_pct: Optional[float] = None
            exit_price: Optional[float] = None
            exit_reason = ""
            candles = _candles_range(cur, symbol, ts, exit_ts)
            if candles and levels.sl and levels.tp:
                sim = simulate_sl_tp_path(
                    entry_price,
                    action,
                    candles,
                    levels.sl,
                    levels.tp,
                    fee_bps_per_side=pf.fee_bps_per_side,
                )
                if sim:
                    net_ret_pct = sim.net_return_pct
                    exit_price = sim.exit_price
                    exit_reason = sim.exit_reason or ""

            if net_ret_pct is None:
                exit_price = _close_price(cur, symbol, exit_ts)
                if exit_price is None or exit_price <= 0:
                    skipped["no_exit"] += 1
                    continue
                gross = (exit_price - entry_price) / entry_price
                if action == "SELL":
                    gross = -gross
                fee = 2.0 * (pf.fee_bps_per_side / 10000.0)
                net_ret_pct = (gross - fee) * 100.0

            # Leveraged PnL on margin slice; cap loss at margin (liquidation).
            leveraged_frac = pos_pct * pf.leverage * (net_ret_pct / 100.0)
            max_loss_frac = -pos_pct
            leveraged_frac = max(leveraged_frac, max_loss_frac)

            pnl_eur = equity * leveraged_frac
            equity = equity + pnl_eur
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
            open_slots.append(exit_ts)
            last_entry_ts = ts
            last_by_symbol[symbol] = ts
            trades_by_symbol[symbol] = trades_by_symbol.get(symbol, 0) + 1
            if pnl_eur < 0:
                loss_streak += 1
                if (
                    cfg.loss_streak_for_cooloff > 0
                    and loss_streak >= cfg.loss_streak_for_cooloff
                ):
                    cooloff_left = cfg.cooloff_skipped_entries
                    loss_streak = 0
            else:
                loss_streak = 0

            trades.append(
                {
                    "symbol": symbol,
                    "action": action,
                    "ts": ts,
                    "confidence": conf,
                    "quality": quality,
                    "win_prob": win_prob,
                    "position_pct": pos_pct * 100.0,
                    "leverage": pf.leverage,
                    "net_return_pct": net_ret_pct,
                    "pnl_eur": pnl_eur,
                    "equity_eur": equity,
                    "exit_reason": exit_reason,
                }
            )

        wins = sum(1 for t in trades if t["pnl_eur"] > 0)
        return {
            "starting_eur": pf.starting_eur,
            "final_eur": round(equity, 2),
            "profit_eur": round(equity - pf.starting_eur, 2),
            "return_pct": round((equity / pf.starting_eur - 1.0) * 100.0, 2),
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "trades": len(trades),
            "win_rate_pct": round((wins / len(trades) * 100.0) if trades else 0.0, 1),
            "leverage": pf.leverage,
            "skipped": skipped,
            "last_trades": trades[-8:],
            "trades_detail": trades,
        }
    finally:
        conn.close()
