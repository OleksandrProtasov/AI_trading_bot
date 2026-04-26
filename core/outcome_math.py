"""Path metrics from OHLC rows for post-hoc signal evaluation (no I/O)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _f(row: Dict[str, Any], key: str) -> float:
    v = row.get(key)
    return float(v) if v is not None else 0.0


def compute_path_metrics(
    entry_price: float,
    action: str,
    candles_asc: List[Dict[str, Any]],
    *,
    direction_threshold_pct: float = 0.05,
) -> Optional[Dict[str, Any]]:
    """
    candles_asc: rows with at least high, low, close (timestamp optional).

    Returns dict with return_pct, max_adverse_pct, max_favorable_pct,
    directional_hit (0/1 or None for WAIT/unknown), close_last; or None if
    entry invalid or no candles.
    """
    if entry_price is None or entry_price <= 0 or not candles_asc:
        return None

    action_u = (action or "").upper()
    lows = [_f(c, "low") for c in candles_asc]
    highs = [_f(c, "high") for c in candles_asc]
    last_close = _f(candles_asc[-1], "close")

    ret = (last_close - entry_price) / entry_price * 100.0

    if action_u == "BUY":
        max_adv = min((low - entry_price) / entry_price * 100.0 for low in lows)
        max_fav = max((high - entry_price) / entry_price * 100.0 for high in highs)
        hit = 1 if ret > direction_threshold_pct else 0
    elif action_u == "SELL":
        max_adv = max((high - entry_price) / entry_price * 100.0 for high in highs)
        max_fav = min((low - entry_price) / entry_price * 100.0 for low in lows)
        hit = 1 if ret < -direction_threshold_pct else 0
    else:
        max_adv = min((low - entry_price) / entry_price * 100.0 for low in lows)
        max_fav = max((high - entry_price) / entry_price * 100.0 for high in highs)
        hit = None

    return {
        "return_pct": ret,
        "max_adverse_pct": max_adv,
        "max_favorable_pct": max_fav,
        "directional_hit": hit,
        "close_at_horizon": last_close,
    }
