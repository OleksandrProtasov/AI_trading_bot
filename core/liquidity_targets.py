"""Pick take-profit at the nearest reachable liquidity pool."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class LiquidityLevel:
    price: float
    kind: str
    strength: float = 1.0


_KIND_ORDER = {
    "stop_cluster": 0,
    "ltf_swing": 1,
    "equal_highs": 1,
    "equal_lows": 1,
    "book_zone": 2,
    "htf_swing": 3,
    "structural": 4,
}


def nearest_swing_liquidity(
    swings: Any,
    side: str,
    entry: float,
    *,
    kind: str = "ltf_swing",
) -> List[LiquidityLevel]:
    """Nearest swing high (long) or low (short) above/below entry."""
    if not swings or entry <= 0:
        return []
    side_l = (side or "").lower()
    out: List[LiquidityLevel] = []
    if side_l == "long":
        highs = sorted(float(s.price) for s in swings if s.kind == "high" and float(s.price) > entry)
        for px in highs:
            out.append(LiquidityLevel(px, kind))
    else:
        lows = sorted(
            (float(s.price) for s in swings if s.kind == "low" and float(s.price) < entry),
            reverse=True,
        )
        for px in lows:
            out.append(LiquidityLevel(px, kind))
    return out


def equal_liquidity_levels(swings: Any, side: str, entry: float, *, tol_pct: float = 0.12) -> List[LiquidityLevel]:
    """Equal highs/lows — classic liquidity pools."""
    if not swings or entry <= 0:
        return []
    side_l = (side or "").lower()
    kind = "equal_highs" if side_l == "long" else "equal_lows"
    prices = [
        float(s.price)
        for s in swings
        if s.kind == ("high" if side_l == "long" else "low")
    ]
    if len(prices) < 2:
        return []
    out: List[LiquidityLevel] = []
    for i, p1 in enumerate(prices):
        cluster = [p1]
        for p2 in prices[i + 1 :]:
            mid = (p1 + p2) / 2.0
            if mid > 0 and abs(p1 - p2) / mid * 100.0 <= tol_pct:
                cluster.append(p2)
        if len(cluster) >= 2:
            level = sum(cluster) / len(cluster)
            if side_l == "long" and level > entry:
                out.append(LiquidityLevel(level, kind, strength=1.2))
            elif side_l == "short" and level < entry:
                out.append(LiquidityLevel(level, kind, strength=1.2))
    return out


def levels_from_stop_clusters(
    clusters: Sequence[dict],
    side: str,
    entry: float,
) -> List[LiquidityLevel]:
    out: List[LiquidityLevel] = []
    side_l = (side or "").lower()
    for c in clusters or []:
        px = float(c.get("price") or 0)
        if px <= 0:
            continue
        ctype = (c.get("type") or "").lower()
        if side_l == "long" and ctype == "short_stop_cluster" and px > entry:
            out.append(LiquidityLevel(px, "stop_cluster", strength=1.3))
        elif side_l == "short" and ctype == "long_stop_cluster" and px < entry:
            out.append(LiquidityLevel(px, "stop_cluster", strength=1.3))
    return out


def levels_from_book_zones(
    zones: Sequence[dict],
    side: str,
    entry: float,
) -> List[LiquidityLevel]:
    out: List[LiquidityLevel] = []
    side_l = (side or "").lower()
    for z in zones or []:
        px = float(z.get("price") or z.get("price_level") or 0)
        if px <= 0:
            continue
        zt = (z.get("type") or z.get("zone_type") or "").lower()
        if side_l == "long" and zt == "resistance" and px > entry:
            out.append(LiquidityLevel(px, "book_zone", strength=0.9))
        elif side_l == "short" and zt == "support" and px < entry:
            out.append(LiquidityLevel(px, "book_zone", strength=0.9))
    return out


def load_recent_book_zones_sync(
    db_path: str,
    symbol: str,
    *,
    max_age_sec: int = 7200,
) -> List[dict]:
    sym = symbol.upper()
    since = int(time.time()) - int(max_age_sec)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT price_level, zone_type, liquidity_amount, timestamp
            FROM liquidity_zones
            WHERE symbol=? AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 40
            """,
            (sym, since),
        ).fetchall()
        return [
            {
                "price": float(r["price_level"]),
                "type": str(r["zone_type"] or ""),
                "amount": float(r["liquidity_amount"] or 0),
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def build_liquidity_levels(
    *,
    side: str,
    entry: float,
    structural_target: float = 0.0,
    ltf_swings: Any = None,
    htf_swings: Any = None,
    stop_clusters: Optional[Sequence[dict]] = None,
    book_zones: Optional[Sequence[dict]] = None,
) -> List[LiquidityLevel]:
    levels: List[LiquidityLevel] = []
    levels.extend(nearest_swing_liquidity(ltf_swings, side, entry, kind="ltf_swing"))
    levels.extend(nearest_swing_liquidity(htf_swings, side, entry, kind="htf_swing"))
    levels.extend(equal_liquidity_levels(ltf_swings, side, entry))
    levels.extend(levels_from_stop_clusters(stop_clusters or [], side, entry))
    levels.extend(levels_from_book_zones(book_zones or [], side, entry))
    st = float(structural_target or 0)
    side_l = (side or "").lower()
    if st > 0:
        if side_l == "long" and st > entry:
            levels.append(LiquidityLevel(st, "structural"))
        elif side_l == "short" and st < entry:
            levels.append(LiquidityLevel(st, "structural"))
    return _dedupe_levels(levels)


def _dedupe_levels(levels: List[LiquidityLevel], *, tol_pct: float = 0.05) -> List[LiquidityLevel]:
    out: List[LiquidityLevel] = []
    for lv in sorted(levels, key=lambda x: x.price):
        if lv.price <= 0:
            continue
        dup = False
        for kept in out:
            mid = (kept.price + lv.price) / 2.0
            if mid > 0 and abs(kept.price - lv.price) / mid * 100.0 <= tol_pct:
                if _KIND_ORDER.get(lv.kind, 9) < _KIND_ORDER.get(kept.kind, 9):
                    out.remove(kept)
                    out.append(lv)
                dup = True
                break
        if not dup:
            out.append(lv)
    return out


def select_tp_at_liquidity(
    entry: float,
    sl: float,
    side: str,
    levels: Sequence[LiquidityLevel],
    *,
    min_rr: float = 2.5,
    min_tp_pct: float = 1.2,
    max_tp_pct: float = 8.0,
) -> Optional[Tuple[float, str, float]]:
    """
    Nearest liquidity pool that satisfies min RR and distance bounds.
    Returns (tp, source_kind, rr_ratio) or None.
    """
    if entry <= 0 or sl <= 0 or not levels:
        return None
    side_l = (side or "").lower()
    if side_l == "long":
        risk = entry - sl
    elif side_l == "short":
        risk = sl - entry
    else:
        return None
    if risk <= 0:
        return None
    sl_pct = risk / entry * 100.0

    candidates: List[Tuple[float, int, float, str, float]] = []
    for lv in levels:
        px = float(lv.price)
        if side_l == "long":
            if px <= entry:
                continue
            tp_pct = (px - entry) / entry * 100.0
        else:
            if px >= entry:
                continue
            tp_pct = (entry - px) / entry * 100.0
        if tp_pct < min_tp_pct - 1e-9 or tp_pct > max_tp_pct + 1e-9:
            continue
        rr = tp_pct / sl_pct if sl_pct > 0 else 0.0
        if rr < min_rr - 1e-9:
            continue
        order = _KIND_ORDER.get(lv.kind, 9)
        candidates.append((tp_pct, order, px, lv.kind, rr))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    _, _, tp, kind, rr = candidates[0]
    return float(tp), str(kind), float(rr)


def liquidity_kind_label(kind: str) -> str:
    return {
        "stop_cluster": "стоп-кластер",
        "ltf_swing": "LTF swing",
        "htf_swing": "HTF swing",
        "equal_highs": "equal highs",
        "equal_lows": "equal lows",
        "book_zone": "стакан",
        "structural": "структура",
        "rr_floor": "min RR",
    }.get(kind or "", kind or "ликвидность")
