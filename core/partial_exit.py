"""Partial take-profit: scale out at TP1, runner to liquidity TP with breakeven."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PartialExitPlan:
    tp1: float
    partial_rr: float
    partial_size: float
    be_after_partial: bool


@dataclass
class PartialHit:
    kind: str  # partial_tp | sl | tp | be | timeout
    exit_price: float
    leg_pnl_pct: float
    net_leg_pnl_pct: float


def compute_tp1(
    entry: float,
    sl: float,
    action: str,
    *,
    partial_rr: float = 1.5,
) -> float:
    act = (action or "").upper()
    if entry <= 0 or sl <= 0 or partial_rr <= 0:
        return 0.0
    if act == "BUY":
        risk = entry - sl
        if risk <= 0:
            return 0.0
        return entry + risk * partial_rr
    if act == "SELL":
        risk = sl - entry
        if risk <= 0:
            return 0.0
        return entry - risk * partial_rr
    return 0.0


def trade_tp_sl_metrics(
    entry: float,
    sl: float,
    tp: float,
    action: str,
) -> tuple[float, float, float]:
    """Returns (sl_pct, tp_pct, rr_ratio)."""
    act = (action or "").upper()
    if entry <= 0 or sl <= 0 or tp <= 0:
        return 0.0, 0.0, 0.0
    if act == "BUY":
        sl_pct = (entry - sl) / entry * 100.0
        tp_pct = (tp - entry) / entry * 100.0
    elif act == "SELL":
        sl_pct = (sl - entry) / entry * 100.0
        tp_pct = (entry - tp) / entry * 100.0
    else:
        return 0.0, 0.0, 0.0
    rr = tp_pct / sl_pct if sl_pct > 0 else 0.0
    return float(sl_pct), float(tp_pct), float(rr)


def should_use_partial_exit(
    entry: float,
    sl: float,
    tp: float,
    action: str,
    *,
    adaptive: bool = True,
    min_tp_pct: float = 3.5,
    min_rr: float = 2.8,
) -> bool:
    """Partial only when full TP is far — preserve runners on close liquidity targets."""
    if not adaptive:
        return True
    _, tp_pct, rr = trade_tp_sl_metrics(entry, sl, tp, action)
    return tp_pct >= min_tp_pct - 1e-9 or rr >= min_rr - 1e-9


def resolve_tp1(
    entry: float,
    sl: float,
    tp: float,
    action: str,
    *,
    partial_enabled: bool = True,
    partial_rr: float = 1.5,
    adaptive: bool = True,
    min_tp_pct: float = 3.5,
    min_rr: float = 2.8,
) -> float:
    """TP1 level or 0 when partial is off for this trade."""
    if not partial_enabled:
        return 0.0
    if not should_use_partial_exit(
        entry, sl, tp, action, adaptive=adaptive, min_tp_pct=min_tp_pct, min_rr=min_rr
    ):
        return 0.0
    return compute_tp1(entry, sl, action, partial_rr=partial_rr)


def partial_plan_from_config(agent: Any) -> PartialExitPlan:
    enabled = bool(getattr(agent, "paper_partial_enabled", True))
    if not enabled:
        return PartialExitPlan(tp1=0.0, partial_rr=0.0, partial_size=0.0, be_after_partial=False)
    return PartialExitPlan(
        tp1=0.0,
        partial_rr=float(getattr(agent, "paper_partial_rr", 1.5)),
        partial_size=float(getattr(agent, "paper_partial_size", 0.5)),
        be_after_partial=bool(getattr(agent, "paper_be_after_partial", True)),
    )


def leg_return_pct(entry: float, exit_price: float, action: str) -> float:
    act = (action or "").upper()
    if entry <= 0:
        return 0.0
    if act == "BUY":
        return (exit_price - entry) / entry * 100.0
    if act == "SELL":
        return (entry - exit_price) / entry * 100.0
    return 0.0


def net_leg_return_pct(
    entry: float, exit_price: float, action: str, *, fee_bps_per_side: float
) -> float:
    gross = leg_return_pct(entry, exit_price, action)
    fee = 2.0 * float(fee_bps_per_side) / 100.0
    return gross - fee


def blend_position_pnl(
    partial_leg_pnl: float,
    final_leg_pnl: float,
    *,
    partial_size: float,
) -> float:
    ps = max(0.0, min(1.0, float(partial_size)))
    return partial_leg_pnl * ps + final_leg_pnl * (1.0 - ps)


def effective_sl(trade: Dict[str, Any]) -> float:
    if int(trade.get("partial_taken") or 0) and float(trade.get("position_pct") or 1.0) < 1.0:
        be = float(trade.get("sl_after_partial") or trade.get("entry") or 0)
        if be > 0:
            return be
    return float(trade["sl"])


def check_live_hit(
    trade: Dict[str, Any],
    price: float,
    *,
    fee_bps_per_side: float,
) -> Optional[PartialHit]:
    entry = float(trade["entry"])
    tp = float(trade["tp"])
    sl = effective_sl(trade)
    act = (trade.get("action") or "").upper()
    partial_taken = int(trade.get("partial_taken") or 0)
    tp1 = float(trade.get("tp1") or 0)

    if act == "BUY":
        if price <= sl:
            kind = "be" if partial_taken and abs(sl - entry) / entry < 0.001 else "sl"
            px = sl
        elif not partial_taken and tp1 > 0 and price >= tp1:
            kind, px = "partial_tp", tp1
        elif price >= tp:
            kind, px = "tp", tp
        else:
            return None
    elif act == "SELL":
        if price >= sl:
            kind = "be" if partial_taken and abs(sl - entry) / entry < 0.001 else "sl"
            px = sl
        elif not partial_taken and tp1 > 0 and price <= tp1:
            kind, px = "partial_tp", tp1
        elif price <= tp:
            kind, px = "tp", tp
        else:
            return None
    else:
        return None

    gross = leg_return_pct(entry, px, act)
    net = gross - 2.0 * fee_bps_per_side / 100.0
    return PartialHit(kind=kind, exit_price=px, leg_pnl_pct=gross, net_leg_pnl_pct=net)


def simulate_partial_path(
    trade: Dict[str, Any],
    candles: List[Dict[str, Any]],
    *,
    fee_bps_per_side: float,
) -> Optional[PartialHit]:
    """Walk 1m candles; partial TP before full TP; BE stop after partial."""
    if not candles:
        return None
    entry = float(trade["entry"])
    tp = float(trade["tp"])
    act = (trade.get("action") or "").upper()
    partial_taken = int(trade.get("partial_taken") or 0)
    tp1 = float(trade.get("tp1") or 0)
    sl = effective_sl(trade)

    for c in candles:
        low = float(c["low"])
        high = float(c["high"])
        if act == "BUY":
            if low <= sl:
                kind = "be" if partial_taken and abs(sl - entry) / entry < 0.001 else "sl"
                return PartialHit(
                    kind=kind,
                    exit_price=sl,
                    leg_pnl_pct=leg_return_pct(entry, sl, act),
                    net_leg_pnl_pct=net_leg_return_pct(entry, sl, act, fee_bps_per_side=fee_bps_per_side),
                )
            if not partial_taken and tp1 > 0 and high >= tp1:
                return PartialHit(
                    kind="partial_tp",
                    exit_price=tp1,
                    leg_pnl_pct=leg_return_pct(entry, tp1, act),
                    net_leg_pnl_pct=net_leg_return_pct(
                        entry, tp1, act, fee_bps_per_side=fee_bps_per_side
                    ),
                )
            if high >= tp:
                return PartialHit(
                    kind="tp",
                    exit_price=tp,
                    leg_pnl_pct=leg_return_pct(entry, tp, act),
                    net_leg_pnl_pct=net_leg_return_pct(
                        entry, tp, act, fee_bps_per_side=fee_bps_per_side
                    ),
                )
        elif act == "SELL":
            if high >= sl:
                kind = "be" if partial_taken and abs(sl - entry) / entry < 0.001 else "sl"
                return PartialHit(
                    kind=kind,
                    exit_price=sl,
                    leg_pnl_pct=leg_return_pct(entry, sl, act),
                    net_leg_pnl_pct=net_leg_return_pct(entry, sl, act, fee_bps_per_side=fee_bps_per_side),
                )
            if not partial_taken and tp1 > 0 and low <= tp1:
                return PartialHit(
                    kind="partial_tp",
                    exit_price=tp1,
                    leg_pnl_pct=leg_return_pct(entry, tp1, act),
                    net_leg_pnl_pct=net_leg_return_pct(
                        entry, tp1, act, fee_bps_per_side=fee_bps_per_side
                    ),
                )
            if low <= tp:
                return PartialHit(
                    kind="tp",
                    exit_price=tp,
                    leg_pnl_pct=leg_return_pct(entry, tp, act),
                    net_leg_pnl_pct=net_leg_return_pct(
                        entry, tp, act, fee_bps_per_side=fee_bps_per_side
                    ),
                )
    return None


@dataclass
class FullTradeSimResult:
    exit_reason: str
    net_pnl_pct: float
    exit_price: float
    partial_taken: bool = False
    partial_pnl_pct: float = 0.0


def _candle_hit(
    trade: Dict[str, Any],
    candle: Dict[str, Any],
    *,
    fee_bps_per_side: float,
    sl_first: bool = True,
) -> Optional[PartialHit]:
    """First touch on a single OHLC bar (conservative: SL before TP)."""
    entry = float(trade["entry"])
    tp = float(trade["tp"])
    sl = effective_sl(trade)
    act = (trade.get("action") or "").upper()
    partial_taken = int(trade.get("partial_taken") or 0)
    tp1 = float(trade.get("tp1") or 0)
    low = float(candle["low"])
    high = float(candle["high"])

    if act == "BUY":
        hit_sl = low <= sl
        hit_tp1 = (not partial_taken) and tp1 > 0 and high >= tp1
        hit_tp = high >= tp
        if hit_sl and (hit_tp1 or hit_tp) and not sl_first:
            hit_sl = False
        if hit_sl:
            kind = "be" if partial_taken and abs(sl - entry) / max(entry, 1e-12) < 0.002 else "sl"
            px = sl
        elif hit_tp1:
            kind, px = "partial_tp", tp1
        elif hit_tp:
            kind, px = "tp", tp
        else:
            return None
    elif act == "SELL":
        hit_sl = high >= sl
        hit_tp1 = (not partial_taken) and tp1 > 0 and low <= tp1
        hit_tp = low <= tp
        if hit_sl and (hit_tp1 or hit_tp) and not sl_first:
            hit_sl = False
        if hit_sl:
            kind = "be" if partial_taken and abs(sl - entry) / max(entry, 1e-12) < 0.002 else "sl"
            px = sl
        elif hit_tp1:
            kind, px = "partial_tp", tp1
        elif hit_tp:
            kind, px = "tp", tp
        else:
            return None
    else:
        return None

    gross = leg_return_pct(entry, px, act)
    net = gross - 2.0 * float(fee_bps_per_side) / 100.0
    return PartialHit(kind=kind, exit_price=px, leg_pnl_pct=gross, net_leg_pnl_pct=net)


def simulate_full_trade_exit(
    entry: float,
    sl: float,
    tp: float,
    action: str,
    candles: List[Dict[str, Any]],
    *,
    partial_enabled: bool = True,
    partial_rr: float = 1.5,
    partial_size: float = 0.5,
    be_after_partial: bool = True,
    fee_bps_per_side: float = 2.0,
    adaptive_partial: bool = True,
    adaptive_min_tp_pct: float = 3.5,
    adaptive_min_rr: float = 2.8,
) -> Optional[FullTradeSimResult]:
    """Full path: optional adaptive partial at TP1, runner to TP/BE/SL, else timeout."""
    if entry <= 0 or sl <= 0 or tp <= 0 or not candles:
        return None

    tp1 = resolve_tp1(
        entry,
        sl,
        tp,
        action,
        partial_enabled=partial_enabled,
        partial_rr=partial_rr,
        adaptive=adaptive_partial,
        min_tp_pct=adaptive_min_tp_pct,
        min_rr=adaptive_min_rr,
    )
    use_partial = partial_enabled and tp1 > 0

    if not use_partial:
        from core.trade_levels import simulate_sl_tp_path

        sim = simulate_sl_tp_path(
            entry, action, candles, sl, tp, fee_bps_per_side=fee_bps_per_side
        )
        if not sim:
            return None
        return FullTradeSimResult(
            exit_reason=sim.exit_reason,
            net_pnl_pct=sim.net_return_pct,
            exit_price=sim.exit_price,
        )

    act = (action or "").upper()
    trade: Dict[str, Any] = {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "tp1": tp1,
        "action": act,
        "partial_taken": 0,
    }
    partial_pnl = 0.0

    for c in candles:
        hit = _candle_hit(trade, c, fee_bps_per_side=fee_bps_per_side)
        if not hit:
            continue
        if hit.kind == "partial_tp":
            partial_pnl = hit.net_leg_pnl_pct
            trade["partial_taken"] = 1
            trade["partial_pnl_pct"] = partial_pnl
            trade["position_pct"] = max(0.0, 1.0 - partial_size)
            if be_after_partial:
                pad = 0.02
                trade["sl_after_partial"] = (
                    entry * (1.0 + pad / 100.0)
                    if act == "BUY"
                    else entry * (1.0 - pad / 100.0)
                )
            continue
        reason = "sl" if hit.kind in ("sl", "be") else hit.kind
        net = (
            blend_position_pnl(partial_pnl, hit.net_leg_pnl_pct, partial_size=partial_size)
            if trade.get("partial_taken")
            else hit.net_leg_pnl_pct
        )
        return FullTradeSimResult(
            exit_reason=reason,
            net_pnl_pct=net,
            exit_price=hit.exit_price,
            partial_taken=bool(trade.get("partial_taken")),
            partial_pnl_pct=partial_pnl,
        )

    last = float(candles[-1]["close"])
    if last <= 0:
        return None
    timeout_net = net_leg_return_pct(entry, last, act, fee_bps_per_side=fee_bps_per_side)
    net = (
        blend_position_pnl(partial_pnl, timeout_net, partial_size=partial_size)
        if trade.get("partial_taken")
        else timeout_net
    )
    return FullTradeSimResult(
        exit_reason="timeout",
        net_pnl_pct=net,
        exit_price=last,
        partial_taken=bool(trade.get("partial_taken")),
        partial_pnl_pct=partial_pnl,
    )
