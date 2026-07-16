"""Asymmetric SL/TP levels and path simulation (tight stop, wide take)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TradeLevels:
    entry: float
    sl: float
    tp: float
    sl_pct: float
    tp_pct: float
    rr_ratio: float


@dataclass
class SlTpSimResult:
    exit_reason: str  # tp | sl | timeout
    return_pct: float
    net_return_pct: float
    directional_hit: int
    exit_price: float
    bars_held: int


def breakeven_win_rate(rr_ratio: float, *, fee_round_trip_pct: float = 0.1) -> float:
    """
    Win rate needed to break even when TP = rr * SL (same % distance).
    fee_round_trip_pct: approximate round-trip cost in %.
    """
    rr = max(0.1, float(rr_ratio))
    sl = 1.0
    tp = rr * sl
    fee = float(fee_round_trip_pct)
    # wr*tp - (1-wr)*sl - fee = 0  =>  wr = (sl+fee)/(tp+sl)
    return (sl + fee) / (tp + sl)


def compute_trade_levels(
    entry: float,
    action: str,
    *,
    sl_pct: float = 0.35,
    tp_rr_ratio: float = 3.0,
    min_sl_pct: float = 0.2,
    max_sl_pct: float = 0.9,
    volatility_pct: Optional[float] = None,
    vol_sl_mult: float = 0.55,
) -> TradeLevels:
    """
    Tight stop + TP at tp_rr_ratio * SL distance.
    Example: SL 0.35%, TP 1.05% => RR 1:3.
    """
    if entry <= 0:
        raise ValueError("entry must be positive")

    eff_sl = float(sl_pct)
    if volatility_pct is not None and volatility_pct > 0:
        eff_sl = max(min_sl_pct, min(max_sl_pct, float(volatility_pct) * vol_sl_mult))
    eff_sl = max(min_sl_pct, min(max_sl_pct, eff_sl))
    tp_pct = eff_sl * float(tp_rr_ratio)
    act = (action or "").upper()

    if act == "BUY":
        sl = entry * (1.0 - eff_sl / 100.0)
        tp = entry * (1.0 + tp_pct / 100.0)
    elif act == "SELL":
        sl = entry * (1.0 + eff_sl / 100.0)
        tp = entry * (1.0 - tp_pct / 100.0)
    else:
        sl = entry
        tp = entry

    rr = tp_pct / eff_sl if eff_sl > 0 else 0.0
    return TradeLevels(
        entry=float(entry),
        sl=float(sl),
        tp=float(tp),
        sl_pct=float(eff_sl),
        tp_pct=float(tp_pct),
        rr_ratio=float(rr),
    )


def passes_min_rr(sl_pct: float, tp_pct: float, *, min_rr: float = 2.5) -> bool:
    if sl_pct <= 0:
        return False
    return (tp_pct / sl_pct) >= float(min_rr)


def _f(row: Dict[str, Any], key: str) -> float:
    v = row.get(key)
    return float(v) if v is not None else 0.0


def simulate_sl_tp_path(
    entry: float,
    action: str,
    candles_asc: List[Dict[str, Any]],
    sl_price: float,
    tp_price: float,
    *,
    fee_bps_per_side: float = 2.0,
    sl_first_on_ambiguous: bool = True,
) -> Optional[SlTpSimResult]:
    """
    Walk forward candle-by-candle; first touch of SL or TP wins.
    On ambiguous bar (both hit), assume SL first (conservative).
    """
    if entry <= 0 or not candles_asc or sl_price <= 0 or tp_price <= 0:
        return None

    act = (action or "").upper()
    fee_pct = 2.0 * float(fee_bps_per_side) / 100.0

    for i, c in enumerate(candles_asc):
        low = _f(c, "low")
        high = _f(c, "high")
        close = _f(c, "close")

        if act == "BUY":
            hit_sl = low <= sl_price
            hit_tp = high >= tp_price
            if hit_sl and hit_tp:
                if sl_first_on_ambiguous:
                    exit_price = sl_price
                    reason = "sl"
                else:
                    exit_price = tp_price
                    reason = "tp"
            elif hit_sl:
                exit_price = sl_price
                reason = "sl"
            elif hit_tp:
                exit_price = tp_price
                reason = "tp"
            else:
                continue
            gross = (exit_price - entry) / entry * 100.0
        elif act == "SELL":
            hit_sl = high >= sl_price
            hit_tp = low <= tp_price
            if hit_sl and hit_tp:
                exit_price = sl_price if sl_first_on_ambiguous else tp_price
                reason = "sl" if sl_first_on_ambiguous else "tp"
            elif hit_sl:
                exit_price = sl_price
                reason = "sl"
            elif hit_tp:
                exit_price = tp_price
                reason = "tp"
            else:
                continue
            gross = (entry - exit_price) / entry * 100.0
        else:
            return None

        net = gross - fee_pct
        hit = 1 if reason == "tp" else 0
        return SlTpSimResult(
            exit_reason=reason,
            return_pct=gross,
            net_return_pct=net,
            directional_hit=hit,
            exit_price=float(exit_price),
            bars_held=i + 1,
        )

    last_close = _f(candles_asc[-1], "close")
    if last_close <= 0:
        return None
    if act == "BUY":
        gross = (last_close - entry) / entry * 100.0
    else:
        gross = (entry - last_close) / entry * 100.0
    net = gross - fee_pct
    return SlTpSimResult(
        exit_reason="timeout",
        return_pct=gross,
        net_return_pct=net,
        directional_hit=1 if net > 0 else 0,
        exit_price=last_close,
        bars_held=len(candles_asc),
    )


def levels_from_data_or_compute(
    entry: float,
    action: str,
    data: Dict[str, Any],
    *,
    sl_pct: float,
    tp_rr_ratio: float,
    volatility_pct: Optional[float] = None,
) -> TradeLevels:
    """Use explicit sl/tp from signal data when valid; else compute."""
    act = (action or "").upper()
    sl_val = data.get("sl")
    tp_val = data.get("tp")
    try:
        sl_f = float(sl_val) if sl_val is not None else 0.0
        tp_f = float(tp_val) if tp_val is not None else 0.0
    except (TypeError, ValueError):
        sl_f = tp_f = 0.0

    if sl_f > 0 and tp_f > 0 and entry > 0:
        if act == "BUY":
            sl_pct_eff = (entry - sl_f) / entry * 100.0
            tp_pct_eff = (tp_f - entry) / entry * 100.0
        elif act == "SELL":
            sl_pct_eff = (sl_f - entry) / entry * 100.0
            tp_pct_eff = (entry - tp_f) / entry * 100.0
        else:
            sl_pct_eff = tp_pct_eff = 0.0
        rr = tp_pct_eff / sl_pct_eff if sl_pct_eff > 0 else 0.0
        return TradeLevels(
            entry=entry,
            sl=sl_f,
            tp=tp_f,
            sl_pct=max(0.0, sl_pct_eff),
            tp_pct=max(0.0, tp_pct_eff),
            rr_ratio=rr,
        )

    return compute_trade_levels(
        entry,
        action,
        sl_pct=sl_pct,
        tp_rr_ratio=tp_rr_ratio,
        volatility_pct=volatility_pct,
    )
