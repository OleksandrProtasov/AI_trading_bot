"""Unified SMC entry + dominance + liquidity TP strategy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.liquidity_targets import liquidity_kind_label
from core.market_context import BookMetrics, build_market_context
from core.order_flow import TradeFlowMetrics
from core.partial_exit import resolve_tp1, should_use_partial_exit
from core.structure_levels import finalize_structure_levels, htf_liquidity_target


@dataclass
class StrategyDecision:
    ok: bool
    block_reason: str = ""
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    tp_source: str = ""
    rr_ratio: float = 0.0
    sl_pct: float = 0.0
    tp_pct: float = 0.0
    entry_mode: str = ""
    zone_low: float = 0.0
    zone_high: float = 0.0
    in_zone: bool = False
    dominance_label: str = ""
    flow_delta_pct: float = 0.0
    book_imbalance: float = 0.0
    exit_plan: str = ""
    checklist: Dict[str, Any] = field(default_factory=dict)


def _price_in_zone(price: float, zone_low: float, zone_high: float, *, tol_pct: float = 0.75) -> bool:
    if price <= 0 or zone_low <= 0 or zone_high <= zone_low:
        return False
    pad_lo = zone_low * (1.0 - tol_pct / 100.0)
    pad_hi = zone_high * (1.0 + tol_pct / 100.0)
    return pad_lo <= price <= pad_hi


def _candle_touched_zone(
    candles: Optional[List[Dict[str, Any]]],
    zone_low: float,
    zone_high: float,
    *,
    lookback: int = 5,
    tol_pct: float = 0.75,
) -> bool:
    """True if any recent candle wick touched the padded zone."""
    if not candles or zone_low <= 0 or zone_high <= zone_low:
        return False
    pad_lo = zone_low * (1.0 - tol_pct / 100.0)
    pad_hi = zone_high * (1.0 + tol_pct / 100.0)
    for c in candles[-max(1, lookback) :]:
        try:
            lo = float(c.get("low") or 0)
            hi = float(c.get("high") or 0)
        except (TypeError, ValueError):
            continue
        if lo <= 0 or hi <= 0:
            continue
        if lo <= pad_hi and hi >= pad_lo:
            return True
    return False


def propose_entry(
    setup: Any,
    action: str,
    current_price: float,
    entry_mode: str,
    *,
    zone_tol_pct: float = 0.75,
    recent_candles: Optional[List[Dict[str, Any]]] = None,
    touch_lookback: int = 5,
) -> Tuple[float, bool, str]:
    """Zone retest entry or continuation at structure price."""
    if not setup or current_price <= 0:
        return 0.0, False, "нет цены"

    zone = getattr(setup, "zone", None)
    mode = (entry_mode or "retest").lower()
    act = (action or "").upper()

    if mode == "continuation":
        return float(current_price), True, "continuation"

    if not zone:
        return 0.0, False, "нет зоны retest"

    zlo = float(zone.low)
    zhi = float(zone.high)
    in_zone = _price_in_zone(current_price, zlo, zhi, tol_pct=zone_tol_pct)
    touched = _candle_touched_zone(
        recent_candles, zlo, zhi, lookback=touch_lookback, tol_pct=zone_tol_pct
    )
    if not in_zone and not touched:
        return (
            0.0,
            False,
            f"цена вне зоны [{zlo:.6g}–{zhi:.6g}]",
        )

    entry = (zlo + zhi) / 2.0
    if in_zone:
        if act == "BUY" and entry > current_price * 1.002:
            entry = current_price
        elif act == "SELL" and entry < current_price * 0.998:
            entry = current_price
        return float(entry), True, "retest в зоне"

    # Wick touch: enter at zone mid (limit-style), price already probed the zone.
    return float(entry), True, "retest касание зоны"


def _dominance_label(
    flow: Optional[TradeFlowMetrics],
    book: Optional[BookMetrics],
    action: str,
) -> str:
    act = (action or "").upper()
    parts: List[str] = []
    if flow and flow.trade_count >= 10:
        if flow.dominance == "buyers":
            parts.append(f"покупатели {flow.delta_pct:+.0f}%")
        elif flow.dominance == "sellers":
            parts.append(f"продавцы {flow.delta_pct:+.0f}%")
        else:
            parts.append(f"нейтрально {flow.delta_pct:+.0f}%")
    if book:
        parts.append(f"стакан {book.imbalance:+.0%}")
    if not parts:
        return "нет данных"
    return " · ".join(parts)


def _exit_plan_text(
    entry: float,
    sl: float,
    tp: float,
    tp_source: str,
    action: str,
    *,
    partial_rr: float = 1.5,
) -> str:
    act = (action or "").upper()
    if entry <= 0 or sl <= 0 or tp <= 0:
        return ""
    if act == "BUY":
        sl_pct = (entry - sl) / entry * 100.0
        tp_pct = (tp - entry) / entry * 100.0
    else:
        sl_pct = (sl - entry) / entry * 100.0
        tp_pct = (entry - tp) / entry * 100.0
    kind = liquidity_kind_label(tp_source) if tp_source else "ликвидность"
    lines = [f"SL {sl_pct:.2f}% → TP {tp_pct:.2f}% ({kind})"]
    if should_use_partial_exit(entry, sl, tp, action):
        tp1 = resolve_tp1(entry, sl, tp, action, partial_rr=partial_rr)
        if tp1 > 0:
            if act == "BUY":
                tp1_pct = (tp1 - entry) / entry * 100.0
            else:
                tp1_pct = (entry - tp1) / entry * 100.0
            lines.append(f"Partial 50% @ {tp1_pct:.2f}% (1.5R), runner до {kind}")
    return " · ".join(lines)


def evaluate_ready_strategy(
    *,
    setup: Any,
    action: str,
    current_price: float,
    entry_mode: str,
    db_path: str,
    symbol: str,
    book_metrics: Optional[BookMetrics] = None,
    flow_metrics: Optional[TradeFlowMetrics] = None,
    ltf_swings: Any = None,
    htf_swings: Any = None,
    stop_clusters: Optional[List[dict]] = None,
    book_zones: Optional[List[dict]] = None,
    volatility_pct: Optional[float] = None,
    min_rr: float = 2.5,
    min_sl_pct: float = 0.55,
    max_sl_pct: float = 2.5,
    min_tp_pct: float = 1.2,
    max_tp_pct: float = 8.0,
    require_book_aligned: bool = True,
    require_flow_aligned: bool = True,
    zone_tol_pct: float = 0.75,
    recent_candles: Optional[List[Dict[str, Any]]] = None,
) -> StrategyDecision:
    """Single decision: zone entry + dominance + structural TP."""
    checklist: Dict[str, Any] = {}
    entry, in_zone, entry_reason = propose_entry(
        setup,
        action,
        current_price,
        entry_mode,
        zone_tol_pct=zone_tol_pct,
        recent_candles=recent_candles,
    )
    zone = getattr(setup, "zone", None) if setup else None
    zlo = float(zone.low) if zone else 0.0
    zhi = float(zone.high) if zone else 0.0

    if not in_zone or entry <= 0:
        return StrategyDecision(
            ok=False,
            block_reason=entry_reason,
            entry_mode=entry_mode,
            zone_low=zlo,
            zone_high=zhi,
            in_zone=False,
            checklist=checklist,
        )

    fl = finalize_structure_levels(
        setup,
        entry,
        action,
        min_rr=min_rr,
        min_sl_pct=min_sl_pct,
        max_sl_pct=max_sl_pct,
        ltf_swings=ltf_swings,
        htf_swings=htf_swings,
        stop_clusters=stop_clusters,
        book_zones=book_zones,
        htf_target=htf_liquidity_target(htf_swings, setup.side, entry),
        min_tp_pct=min_tp_pct,
        max_tp_pct=max_tp_pct,
        volatility_pct=volatility_pct,
    )
    if not fl:
        return StrategyDecision(
            ok=False,
            block_reason="не удалось построить SL/TP",
            entry=entry,
            zone_low=zlo,
            zone_high=zhi,
            in_zone=True,
            entry_mode=entry_mode,
        )

    ctx = build_market_context(
        action=action,
        setup=setup,
        entry=entry,
        sl=fl.sl,
        tp=fl.tp,
        entry_mode=entry_mode,
        book_metrics=book_metrics,
        db_path=db_path,
        symbol=symbol,
    )

    flow = flow_metrics or TradeFlowMetrics()
    dom = _dominance_label(flow, book_metrics, action)
    checklist.update(
        {
            "in_zone": True,
            "zone_low": zlo,
            "zone_high": zhi,
            "flow_delta_pct": round(flow.delta_pct, 2),
            "flow_aligned": flow.flow_aligned,
            "flow_dominance": flow.dominance,
            "book_imbalance": round((book_metrics.imbalance if book_metrics else 0.0), 3),
            "book_aligned": bool(book_metrics and book_metrics.book_aligned),
            "tp_source": fl.tp_source,
            "exit_plan": _exit_plan_text(entry, fl.sl, fl.tp, fl.tp_source, action),
        }
    )
    if ctx.oi.available:
        checklist["oi_change_pct"] = round(ctx.oi.change_pct, 2)
        checklist["oi_aligned"] = ctx.oi.oi_aligned

    reasons: List[str] = []
    if not ctx.gate_ok:
        reasons.append(ctx.gate_reason or "market gate")
    if require_book_aligned and book_metrics and not book_metrics.book_aligned:
        # Only hard-block when book clearly opposes (ignore missing/thin as soft).
        if not book_metrics.thin_book:
            reasons.append("стакан против сделки")
    if require_flow_aligned and flow.trade_count >= 15 and not flow.flow_aligned:
        reasons.append(f"поток против ({flow.delta_pct:+.1f}%)")
    # Insufficient trade sample: do not block (soft pass).

    if reasons:
        return StrategyDecision(
            ok=False,
            block_reason="; ".join(reasons),
            entry=entry,
            sl=fl.sl,
            tp=fl.tp,
            tp_source=fl.tp_source,
            rr_ratio=fl.rr_ratio,
            sl_pct=fl.sl_pct,
            tp_pct=fl.tp_pct,
            entry_mode=entry_mode,
            zone_low=zlo,
            zone_high=zhi,
            in_zone=True,
            dominance_label=dom,
            flow_delta_pct=flow.delta_pct,
            book_imbalance=book_metrics.imbalance if book_metrics else 0.0,
            exit_plan=checklist.get("exit_plan", ""),
            checklist=checklist,
        )

    return StrategyDecision(
        ok=True,
        entry=entry,
        sl=fl.sl,
        tp=fl.tp,
        tp_source=fl.tp_source,
        rr_ratio=fl.rr_ratio,
        sl_pct=fl.sl_pct,
        tp_pct=fl.tp_pct,
        entry_mode=entry_mode,
        zone_low=zlo,
        zone_high=zhi,
        in_zone=True,
        dominance_label=dom,
        flow_delta_pct=flow.delta_pct,
        book_imbalance=book_metrics.imbalance if book_metrics else 0.0,
        exit_plan=checklist.get("exit_plan", ""),
        checklist=checklist,
    )
