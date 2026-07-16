"""Retest state machine: sweep → BOS → await retest → ready."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.smc_analysis import (
    Side,
    Trend,
    Zone,
    classify_htf_trend,
    detect_bos,
    detect_liquidity_sweep,
    find_swings,
    is_mid_range,
    pick_retest_zone,
    range_position,
    structural_sl_tp,
)


@dataclass
class StructureSetup:
    symbol: str
    side: Side
    state: str  # await_bos | await_retest | ready
    trend: Trend
    sweep_price: float
    sweep_index: int
    bos_price: float
    zone: Zone
    invalidation: float
    target_price: float
    created_ts: int
    bos_index: int = -1
    ready_ts: Optional[int] = None
    checklist: Dict[str, bool] = field(default_factory=dict)


@dataclass
class RetestConfig:
    htf_minutes: int = 240
    ltf_minutes: int = 15
    min_rr: float = 3.0
    range_edge_pct: float = 0.30
    block_mid_range_pct: float = 0.40
    retest_tolerance_pct: float = 0.15
    setup_ttl_sec: int = 14400
    swing_wing: int = 2
    require_htf_trend: bool = True
    block_range_trend: bool = True
    continuation_enabled: bool = True
    continuation_min_hold_bars: int = 4
    continuation_min_wait_bars: int = 6
    continuation_min_displacement_pct: float = 0.25


def _next_target(
    swings,
    side: Side,
    htf_swings: Optional[Any] = None,
    *,
    reference_price: float = 0.0,
) -> float:
    """Nearest liquidity swing in trade direction (not the furthest HTF extreme)."""
    ref = float(reference_price or 0.0)
    if side == "long":
        for src in (swings, htf_swings):
            if not src:
                continue
            highs = sorted(
                float(s.price) for s in src if s.kind == "high" and float(s.price) > ref
            )
            if highs:
                return highs[0]
        highs = [float(s.price) for s in (swings or []) if s.kind == "high"]
        return highs[-1] if highs else 0.0
    for src in (swings, htf_swings):
        if not src:
            continue
        lows = sorted(
            (float(s.price) for s in src if s.kind == "low" and float(s.price) < ref),
            reverse=True,
        )
        if lows:
            return lows[0]
    lows = [float(s.price) for s in (swings or []) if s.kind == "low"]
    return lows[-1] if lows else 0.0


def price_in_zone(price: float, zone: Zone) -> bool:
    return float(zone.low) <= float(price) <= float(zone.high)


def detect_retest_touch(
    candles: List[Dict[str, Any]],
    *,
    side: Side,
    zone: Zone,
    invalidation: float,
    lookback: int = 4,
) -> bool:
    """True if any recent LTF bar retested the zone and held above/below invalidation."""
    if not candles:
        return False
    window = candles[-max(1, lookback) :]
    for c in window:
        low = float(c["low"])
        high = float(c["high"])
        close = float(c["close"])
        if side == "long":
            touched = low <= float(zone.high) and low >= float(zone.low) * 0.998
            held = close > invalidation and close >= float(zone.low)
            if touched and held:
                return True
        else:
            touched = high >= float(zone.low) and high <= float(zone.high) * 1.002
            held = close < invalidation and close <= float(zone.high)
            if touched and held:
                return True
    return False


def _zone_touched_since_bos(
    candles: List[Dict[str, Any]],
    bos_index: int,
    zone: Zone,
    side: Side,
) -> bool:
    if bos_index < 0:
        return False
    for c in candles[bos_index + 1 :]:
        low = float(c["low"])
        high = float(c["high"])
        if side == "long":
            if low <= float(zone.high) and low >= float(zone.low) * 0.997:
                return True
        elif high >= float(zone.low) and high <= float(zone.high) * 1.003:
            return True
    return False


def detect_continuation_hold(
    candles: List[Dict[str, Any]],
    *,
    side: Side,
    zone: Zone,
    bos_price: float,
    bos_index: int,
    invalidation: float,
    min_hold_bars: int = 4,
    min_wait_bars: int = 6,
    min_displacement_pct: float = 0.25,
) -> bool:
    """
    Breakaway continuation: BOS happened, price never retested the zone,
    holds above/below BOS with displacement — stricter than retest entry.
    """
    if not candles or bos_index < 0 or bos_price <= 0:
        return False
    bars_since = len(candles) - 1 - bos_index
    if bars_since < min_wait_bars:
        return False
    if _zone_touched_since_bos(candles, bos_index, zone, side):
        return False

    segment = candles[bos_index + 1 :]
    if len(segment) < min_hold_bars:
        return False
    recent = segment[-min_hold_bars:]
    close = float(candles[-1]["close"])
    disp_mult = 1.0 + min_displacement_pct / 100.0
    disp_mult_short = 1.0 - min_displacement_pct / 100.0

    if side == "long":
        if close <= bos_price * disp_mult:
            return False
        return all(
            float(c["close"]) > bos_price and float(c["low"]) >= invalidation
            for c in recent
        )
    if close >= bos_price * disp_mult_short:
        return False
    return all(
        float(c["close"]) < bos_price and float(c["high"]) <= invalidation
        for c in recent
    )


class StructureSetupStore:
    def __init__(self, cfg: Optional[RetestConfig] = None) -> None:
        self.cfg = cfg or RetestConfig()
        self._setups: Dict[str, StructureSetup] = {}

    def get(self, symbol: str) -> Optional[StructureSetup]:
        sym = symbol.upper()
        setup = self._setups.get(sym)
        if not setup:
            return None
        if int(time.time()) - setup.created_ts > self.cfg.setup_ttl_sec:
            del self._setups[sym]
            return None
        return setup

    def update(
        self,
        symbol: str,
        ltf_candles: List[Dict[str, Any]],
        htf_candles: List[Dict[str, Any]],
        *,
        m1_candles: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[StructureSetup]:
        sym = symbol.upper()
        if len(ltf_candles) < 40:
            return self.get(sym)

        now = int(time.time())
        self._expire_old(now)

        ltf_swings = find_swings(ltf_candles, wing=self.cfg.swing_wing)
        htf_swings = find_swings(htf_candles, wing=self.cfg.swing_wing)
        trend = classify_htf_trend(htf_swings)
        pos = range_position(ltf_candles)

        existing = self.get(sym)
        if existing and existing.state == "ready":
            return existing

        px = float(ltf_candles[-1]["close"])

        if existing and existing.state == "await_retest":
            retest_bars = m1_candles if m1_candles and len(m1_candles) >= 5 else ltf_candles
            retest_lookback = 30 if m1_candles and len(m1_candles) >= 5 else 4
            if detect_retest_touch(
                retest_bars,
                side=existing.side,
                zone=existing.zone,
                invalidation=existing.invalidation,
                lookback=retest_lookback,
            ):
                existing.state = "ready"
                existing.ready_ts = now
                existing.checklist["retest"] = True
                existing.checklist["continuation"] = False
                self._setups[sym] = existing
            elif self.cfg.continuation_enabled and existing.bos_index >= 0:
                if detect_continuation_hold(
                    ltf_candles,
                    side=existing.side,
                    zone=existing.zone,
                    bos_price=existing.bos_price,
                    bos_index=existing.bos_index,
                    invalidation=existing.invalidation,
                    min_hold_bars=self.cfg.continuation_min_hold_bars,
                    min_wait_bars=self.cfg.continuation_min_wait_bars,
                    min_displacement_pct=self.cfg.continuation_min_displacement_pct,
                ):
                    existing.state = "ready"
                    existing.ready_ts = now
                    existing.checklist["continuation"] = True
                    existing.checklist["retest"] = False
                    self._setups[sym] = existing
            return existing

        if existing and existing.state == "await_bos":
            bos = detect_bos(
                ltf_candles, ltf_swings, side=existing.side, after_index=existing.sweep_index
            )
            if bos:
                zone = pick_retest_zone(
                    ltf_candles,
                    side=existing.side,
                    sweep_price=existing.sweep_price,
                    tolerance_pct=self.cfg.retest_tolerance_pct,
                )
                ref_px = float(ltf_candles[-1]["close"]) if ltf_candles else 0.0
                target = _next_target(
                    ltf_swings, existing.side, htf_swings, reference_price=ref_px
                )
                existing.state = "await_retest"
                existing.bos_price = bos.price
                existing.bos_index = bos.index
                existing.zone = zone
                existing.target_price = target
                existing.checklist["bos"] = True
                existing.checklist["zone"] = True
                self._setups[sym] = existing
            return existing

        if self.cfg.block_range_trend and trend == "range":
            return existing
        if is_mid_range(pos, block_mid_pct=self.cfg.block_mid_range_pct):
            return existing

        for side in ("long", "short"):
            if self.cfg.require_htf_trend:
                if side == "long" and trend != "up":
                    continue
                if side == "short" and trend != "down":
                    continue

            sweep = detect_liquidity_sweep(ltf_candles, ltf_swings, side=side)
            if not sweep:
                continue

            invalidation = sweep.price
            if side == "long":
                invalidation = min(float(c["low"]) for c in ltf_candles[-5:])
            else:
                invalidation = max(float(c["high"]) for c in ltf_candles[-5:])

            setup = StructureSetup(
                symbol=sym,
                side=side,
                state="await_bos",
                trend=trend,
                sweep_price=sweep.price,
                sweep_index=sweep.index,
                bos_price=0.0,
                zone=pick_retest_zone(
                    ltf_candles,
                    side=side,
                    sweep_price=sweep.price,
                    tolerance_pct=self.cfg.retest_tolerance_pct,
                ),
                invalidation=invalidation,
                target_price=_next_target(
                    ltf_swings,
                    side,
                    htf_swings,
                    reference_price=float(ltf_candles[-1]["close"]),
                ),
                created_ts=now,
                checklist={
                    "trend": trend in ("up", "down"),
                    "not_mid_range": not is_mid_range(
                        pos, block_mid_pct=self.cfg.block_mid_range_pct
                    ),
                    "liquidity_sweep": True,
                    "zone": True,
                },
            )
            self._setups[sym] = setup
            return setup

        return existing

    def _expire_old(self, now: int) -> None:
        dead = [
            k
            for k, v in self._setups.items()
            if now - v.created_ts > self.cfg.setup_ttl_sec
        ]
        for k in dead:
            del self._setups[k]


def setup_allows_action(setup: Optional[StructureSetup], action: str) -> bool:
    if not setup or setup.state != "ready":
        return False
    act = (action or "").upper()
    if setup.side == "long" and act == "BUY":
        return True
    if setup.side == "short" and act == "SELL":
        return True
    return False


def levels_from_setup(setup: StructureSetup, entry: float, min_rr: float) -> tuple[float, float, float]:
    return structural_sl_tp(
        entry,
        setup.side,
        invalidation=setup.invalidation,
        target=setup.target_price,
        min_rr=min_rr,
    )
