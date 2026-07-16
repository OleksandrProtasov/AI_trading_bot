"""SMC-style structure: swings, trend, sweep, BOS, zones."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

Trend = Literal["up", "down", "range"]
Side = Literal["long", "short"]


@dataclass
class SwingPoint:
    kind: Literal["high", "low"]
    price: float
    index: int
    timestamp: int


@dataclass
class Zone:
    kind: str  # ob | fvg | sweep
    low: float
    high: float
    mid: float


@dataclass
class StructureEvent:
    side: Side
    event: str  # sweep | bos | choch
    price: float
    index: int


def find_swings(candles: List[Dict[str, Any]], wing: int = 2) -> List[SwingPoint]:
    out: List[SwingPoint] = []
    n = len(candles)
    if n < wing * 2 + 1:
        return out
    for i in range(wing, n - wing):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        ts = int(candles[i]["timestamp"])
        left_highs = [float(candles[j]["high"]) for j in range(i - wing, i)]
        right_highs = [float(candles[j]["high"]) for j in range(i + 1, i + wing + 1)]
        left_lows = [float(candles[j]["low"]) for j in range(i - wing, i)]
        right_lows = [float(candles[j]["low"]) for j in range(i + 1, i + wing + 1)]
        is_high = h > max(left_highs + right_highs, default=-1e18)
        is_low = l < min(left_lows + right_lows, default=1e18)
        if is_high:
            out.append(SwingPoint("high", h, i, ts))
        elif is_low:
            out.append(SwingPoint("low", l, i, ts))
    return out


def classify_htf_trend(swings: List[SwingPoint]) -> Trend:
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    h1, h2 = highs[-2].price, highs[-1].price
    l1, l2 = lows[-2].price, lows[-1].price
    if h2 > h1 and l2 > l1:
        return "up"
    if h2 < h1 and l2 < l1:
        return "down"
    return "range"


def range_position(candles: List[Dict[str, Any]], lookback: int = 96) -> float:
    """0=range low, 1=range high (recent window)."""
    if not candles:
        return 0.5
    window = candles[-lookback:] if len(candles) >= lookback else candles
    lo = min(float(c["low"]) for c in window)
    hi = max(float(c["high"]) for c in window)
    if hi <= lo:
        return 0.5
    px = float(window[-1]["close"])
    return (px - lo) / (hi - lo)


def is_mid_range(pos: float, *, block_mid_pct: float = 0.40) -> bool:
    edge = (1.0 - float(block_mid_pct)) / 2.0
    return edge < pos < (1.0 - edge)


def detect_liquidity_sweep(
    candles: List[Dict[str, Any]],
    swings: List[SwingPoint],
    *,
    side: Side,
    lookback_bars: int = 12,
) -> Optional[StructureEvent]:
    if len(candles) < lookback_bars + 10:
        return None
    recent = candles[-lookback_bars:]
    start_idx = len(candles) - lookback_bars
    prior = candles[-(lookback_bars + 40) : -lookback_bars]
    if len(prior) < 5:
        prior = candles[:-lookback_bars]

    if side == "long":
        ref_level = min(float(c["low"]) for c in prior[-40:])
        for i, c in enumerate(recent):
            low = float(c["low"])
            close = float(c["close"])
            if low < ref_level * 0.9995 and close > ref_level:
                return StructureEvent("long", "sweep", ref_level, start_idx + i)
    else:
        ref_level = max(float(c["high"]) for c in prior[-40:])
        for i, c in enumerate(recent):
            high = float(c["high"])
            close = float(c["close"])
            if high > ref_level * 1.0005 and close < ref_level:
                return StructureEvent("short", "sweep", ref_level, start_idx + i)
    return None


def detect_bos(
    candles: List[Dict[str, Any]],
    swings: List[SwingPoint],
    *,
    side: Side,
    after_index: int,
) -> Optional[StructureEvent]:
    if not candles:
        return None
    close = float(candles[-1]["close"])
    if side == "long":
        highs = [s for s in swings if s.kind == "high" and s.index >= after_index]
        if not highs:
            highs = [s for s in swings if s.kind == "high"]
        if not highs:
            return None
        level = highs[-1].price
        if close > level * 1.0002:
            return StructureEvent("long", "bos", level, len(candles) - 1)
    else:
        lows = [s for s in swings if s.kind == "low" and s.index >= after_index]
        if not lows:
            lows = [s for s in swings if s.kind == "low"]
        if not lows:
            return None
        level = lows[-1].price
        if close < level * 0.9998:
            return StructureEvent("short", "bos", level, len(candles) - 1)
    return None


def find_bullish_fvg(candles: List[Dict[str, Any]], lookback: int = 30) -> Optional[Zone]:
    if len(candles) < 3:
        return None
    window = candles[-lookback:]
    for i in range(2, len(window)):
        c0 = window[i - 2]
        c2 = window[i]
        gap_low = float(c0["high"])
        gap_high = float(c2["low"])
        if gap_high > gap_low * 1.0003:
            return Zone("fvg", gap_low, gap_high, (gap_low + gap_high) / 2.0)
    return None


def find_bearish_fvg(candles: List[Dict[str, Any]], lookback: int = 30) -> Optional[Zone]:
    if len(candles) < 3:
        return None
    window = candles[-lookback:]
    for i in range(2, len(window)):
        c0 = window[i - 2]
        c2 = window[i]
        gap_high = float(c0["low"])
        gap_low = float(c2["high"])
        if gap_low < gap_high * 0.9997:
            return Zone("fvg", gap_low, gap_high, (gap_low + gap_high) / 2.0)
    return None


def find_order_block(candles: List[Dict[str, Any]], side: Side, lookback: int = 20) -> Optional[Zone]:
    if len(candles) < 4:
        return None
    window = candles[-lookback:]
    for i in range(len(window) - 2, 0, -1):
        c = window[i]
        nxt = window[i + 1]
        o, cl = float(c["open"]), float(c["close"])
        no, ncl = float(nxt["open"]), float(nxt["close"])
        if side == "long" and cl < o and ncl > no and ncl > cl:
            low = min(float(c["low"]), float(c["high"]))
            high = max(float(c["open"]), float(c["close"]))
            return Zone("ob", low, high, (low + high) / 2.0)
        if side == "short" and cl > o and ncl < no and ncl < cl:
            low = min(float(c["open"]), float(c["close"]))
            high = max(float(c["low"]), float(c["high"]))
            return Zone("ob", low, high, (low + high) / 2.0)
    return None


def pick_retest_zone(
    candles: List[Dict[str, Any]],
    *,
    side: Side,
    sweep_price: float,
    tolerance_pct: float = 0.15,
) -> Zone:
    if side == "long":
        ob = find_order_block(candles, "long")
        fvg = find_bullish_fvg(candles)
    else:
        ob = find_order_block(candles, "short")
        fvg = find_bearish_fvg(candles)
    if ob:
        return ob
    if fvg:
        return fvg
    pad = sweep_price * (tolerance_pct / 100.0)
    if side == "long":
        return Zone("sweep", sweep_price, sweep_price + pad, sweep_price + pad / 2.0)
    return Zone("sweep", sweep_price - pad, sweep_price, sweep_price - pad / 2.0)


def structural_sl_tp(
    entry: float,
    side: Side,
    *,
    invalidation: float,
    target: float,
    min_rr: float = 3.0,
) -> Tuple[float, float, float]:
    """Returns (sl, tp, rr). TP is max(structural target, min_rr * risk)."""
    if entry <= 0:
        return entry, entry, 0.0
    if side == "long":
        sl = invalidation * 0.999
        risk = entry - sl
        if risk <= 0:
            return sl, entry, 0.0
        tp_struct = target if target > entry else entry + risk * min_rr
        tp_min = entry + risk * min_rr
        tp = max(tp_struct, tp_min)
        rr = (tp - entry) / risk
        return sl, tp, rr
    sl = invalidation * 1.001
    risk = sl - entry
    if risk <= 0:
        return sl, entry, 0.0
    tp_struct = target if target < entry else entry - risk * min_rr
    tp_min = entry - risk * min_rr
    tp = min(tp_struct, tp_min)
    rr = (entry - tp) / risk
    return sl, tp, rr
