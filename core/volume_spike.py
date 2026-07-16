"""Significant volume spikes tied to SMC setups (tradable impulses)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence


@dataclass
class VolumeSpikeMetrics:
    ratio: float = 0.0
    current_volume: float = 0.0
    avg_volume: float = 0.0
    price: float = 0.0
    price_change_pct: float = 0.0
    direction: str = "neutral"  # up | down | neutral
    candle_range_pct: float = 0.0
    lookback: int = 20


@dataclass
class TradableVolumeSpike:
    metrics: VolumeSpikeMetrics
    setup_state: str
    setup_side: str
    trade_hint: str
    aligned: bool = True


def detect_volume_spike(
    candles: Sequence[Dict[str, Any]],
    *,
    lookback: int = 20,
    min_ratio: float = 2.5,
    min_price_move_pct: float = 0.0,
) -> Optional[VolumeSpikeMetrics]:
    """Spike when last 1m volume >= min_ratio * mean of prior `lookback` bars."""
    if not candles or len(candles) < lookback + 1:
        return None

    window = candles[-(lookback + 1) :]
    current = window[-1]
    prior = window[:-1]

    try:
        cur_vol = float(current.get("volume") or 0)
        px = float(current.get("close") or 0)
        op = float(current.get("open") or px)
        hi = float(current.get("high") or px)
        lo = float(current.get("low") or px)
    except (TypeError, ValueError):
        return None

    if cur_vol <= 0 or px <= 0:
        return None

    vols = []
    for c in prior:
        try:
            v = float(c.get("volume") or 0)
            if v > 0:
                vols.append(v)
        except (TypeError, ValueError):
            continue
    if len(vols) < max(5, lookback // 2):
        return None

    avg = sum(vols) / len(vols)
    if avg <= 0:
        return None

    ratio = cur_vol / avg
    if ratio < min_ratio:
        return None

    chg = (px - op) / op * 100.0 if op > 0 else 0.0
    rng = (hi - lo) / px * 100.0 if px > 0 else 0.0
    direction = "neutral"
    if chg >= 0.05:
        direction = "up"
    elif chg <= -0.05:
        direction = "down"

    if min_price_move_pct > 0 and abs(chg) < min_price_move_pct:
        return None
    if min_price_move_pct > 0 and direction == "neutral":
        return None

    return VolumeSpikeMetrics(
        ratio=ratio,
        current_volume=cur_vol,
        avg_volume=avg,
        price=px,
        price_change_pct=chg,
        direction=direction,
        candle_range_pct=rng,
        lookback=lookback,
    )


def _volume_aligns_with_setup(side: str, direction: str) -> bool:
    s = (side or "").lower()
    if s == "long":
        return direction == "up"
    if s == "short":
        return direction == "down"
    return False


def _trade_hint(setup_state: str, side: str) -> str:
    s = (side or "").lower()
    side_ru = "LONG" if s == "long" else "SHORT" if s == "short" else ""
    if setup_state == "await_bos":
        return f"импульс объёма → возможный BOS ({side_ru})"
    if setup_state == "await_retest":
        return f"импульс объёма → движение к retest ({side_ru})"
    if setup_state == "ready":
        return f"импульс подтверждает сетап ({side_ru})"
    return "импульс объёма на активном сетапе"


def evaluate_tradable_volume_spike(
    candles: Sequence[Dict[str, Any]],
    setup: Any,
    *,
    lookback: int = 20,
    min_ratio: float = 5.0,
    min_price_move_pct: float = 0.35,
    min_candle_range_pct: float = 0.40,
    require_setup: bool = True,
) -> Optional[TradableVolumeSpike]:
    """
    Significant volume + price impulse aligned with an active SMC setup.
    """
    if require_setup:
        if not setup:
            return None
        state = str(getattr(setup, "state", "") or "")
        if state not in ("await_bos", "await_retest", "ready"):
            return None
    else:
        state = str(getattr(setup, "state", "") or "") if setup else ""

    side = str(getattr(setup, "side", "") or "") if setup else ""
    spike = detect_volume_spike(
        candles,
        lookback=lookback,
        min_ratio=min_ratio,
        min_price_move_pct=min_price_move_pct,
    )
    if not spike:
        return None

    if min_candle_range_pct > 0 and spike.candle_range_pct < min_candle_range_pct:
        return None

    if side and not _volume_aligns_with_setup(side, spike.direction):
        return None

    return TradableVolumeSpike(
        metrics=spike,
        setup_state=state,
        setup_side=side,
        trade_hint=_trade_hint(state, side),
        aligned=True,
    )
