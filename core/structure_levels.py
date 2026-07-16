"""Finalize structural SL/TP: SMC + volatility floor + liquidity target."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, List, Optional, Sequence

from core.liquidity_targets import (
    LiquidityLevel,
    build_liquidity_levels,
    select_tp_at_liquidity,
)
from core.smc_retest import StructureSetup, levels_from_setup
from core.trade_levels import TradeLevels, compute_trade_levels, passes_min_rr


@dataclass
class FinalizedLevels:
    sl: float
    tp: float
    rr_ratio: float
    sl_pct: float
    tp_pct: float
    widened_sl: bool = False
    tp_source: str = "rr_floor"


def htf_liquidity_target(
    htf_swings: Any,
    side: str,
    entry: float,
) -> float:
    """Next HTF swing high (long) or low (short) above/below entry."""
    if not htf_swings or entry <= 0:
        return 0.0
    side_l = (side or "").lower()
    if side_l == "long":
        highs = sorted(
            float(s.price) for s in htf_swings if s.kind == "high" and float(s.price) > entry
        )
        return highs[0] if highs else 0.0
    lows = sorted(
        (float(s.price) for s in htf_swings if s.kind == "low" and float(s.price) < entry),
        reverse=True,
    )
    return lows[0] if lows else 0.0


def finalize_structure_levels(
    setup: StructureSetup,
    entry: float,
    action: str,
    *,
    min_rr: float = 3.0,
    min_sl_pct: float = 0.55,
    sl_pct: float = 0.35,
    tp_rr_ratio: float = 3.0,
    volatility_pct: Optional[float] = None,
    vol_sl_mult: float = 0.55,
    htf_target: float = 0.0,
    liquidity_levels: Optional[Sequence[LiquidityLevel]] = None,
    ltf_swings: Any = None,
    htf_swings: Any = None,
    stop_clusters: Optional[Sequence[dict]] = None,
    book_zones: Optional[Sequence[dict]] = None,
    min_tp_pct: float = 1.2,
    max_tp_pct: float = 8.0,
    max_sl_pct: float = 2.5,
) -> Optional[FinalizedLevels]:
    """
    Structural SL with volatility floor.
    TP at the nearest liquidity pool that satisfies min RR (not furthest HTF extreme).
    """
    if not setup or entry <= 0:
        return None

    act = (action or "").upper()
    side = (setup.side or "").lower()
    if (act == "BUY" and side != "long") or (act == "SELL" and side != "short"):
        return None

    structural_target = float(setup.target_price or 0.0)
    if htf_target > 0:
        if side == "long" and htf_target > entry:
            structural_target = htf_target
        elif side == "short" and htf_target < entry:
            structural_target = htf_target

    setup_adj = setup
    if structural_target > 0 and structural_target != float(setup.target_price or 0):
        setup_adj = replace(setup, target_price=structural_target)

    sl_s, _, _ = levels_from_setup(setup_adj, float(entry), min_rr)

    vol: TradeLevels = compute_trade_levels(
        float(entry),
        act,
        sl_pct=sl_pct,
        tp_rr_ratio=tp_rr_ratio,
        volatility_pct=volatility_pct,
        min_sl_pct=min_sl_pct,
        vol_sl_mult=vol_sl_mult,
    )

    widened = False
    if act == "BUY":
        if entry <= sl_s:
            return None
        sl = min(sl_s, vol.sl)
        widened = sl < sl_s - 1e-12
        risk = entry - sl
        if risk <= 0:
            return None
        sl_dist = risk / entry * 100.0
        if sl_dist < min_sl_pct - 1e-9:
            sl = entry * (1.0 - min_sl_pct / 100.0)
            risk = entry - sl
            sl_dist = min_sl_pct
            widened = True
        if sl_dist > max_sl_pct + 1e-9:
            return None
        tp_floor = entry + risk * min_rr
    elif act == "SELL":
        if entry >= sl_s:
            return None
        sl = max(sl_s, vol.sl)
        widened = sl > sl_s + 1e-12
        risk = sl - entry
        if risk <= 0:
            return None
        sl_dist = risk / entry * 100.0
        if sl_dist < min_sl_pct - 1e-9:
            sl = entry * (1.0 + min_sl_pct / 100.0)
            risk = sl - entry
            sl_dist = min_sl_pct
            widened = True
        if sl_dist > max_sl_pct + 1e-9:
            return None
        tp_floor = entry - risk * min_rr
    else:
        return None

    levels: List[LiquidityLevel] = list(liquidity_levels or [])
    if not levels:
        levels = build_liquidity_levels(
            side=side,
            entry=float(entry),
            structural_target=structural_target,
            ltf_swings=ltf_swings,
            htf_swings=htf_swings,
            stop_clusters=stop_clusters,
            book_zones=book_zones,
        )

    picked = select_tp_at_liquidity(
        float(entry),
        float(sl),
        side,
        levels,
        min_rr=min_rr,
        min_tp_pct=min_tp_pct,
        max_tp_pct=max_tp_pct,
    )
    if picked:
        tp, tp_source, rr = picked
        tp_pct = (
            (tp - entry) / entry * 100.0 if act == "BUY" else (entry - tp) / entry * 100.0
        )
    else:
        tp = float(tp_floor)
        tp_source = "rr_floor"
        tp_pct = (
            (tp - entry) / entry * 100.0 if act == "BUY" else (entry - tp) / entry * 100.0
        )
        if tp_pct > max_tp_pct + 1e-9:
            return None
        rr = tp_pct / sl_dist if sl_dist > 0 else 0.0

    if not passes_min_rr(sl_dist, tp_pct, min_rr=min_rr):
        return None

    return FinalizedLevels(
        sl=float(sl),
        tp=float(tp),
        rr_ratio=float(rr),
        sl_pct=float(sl_dist),
        tp_pct=float(tp_pct),
        widened_sl=widened,
        tp_source=str(tp_source),
    )
