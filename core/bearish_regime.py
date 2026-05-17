"""Detect elevated sell/exit pressure — block counter-trend BUYs."""
from __future__ import annotations


def is_bearish_regime(
    *,
    buy_count: int,
    sell_count: int,
    exit_count: int,
    emergency_count: int,
    bearish_pressure: int,
    buy_score: float,
    sell_score: float,
) -> bool:
    """
    True when short-term context favors risk-off / sells over new longs.
    """
    if emergency_count >= 1 and sell_count > 0 and sell_score >= buy_score * 0.85:
        return True
    if bearish_pressure >= 2 and sell_score > buy_score:
        return True
    if exit_count >= 2 and buy_score > 0 and sell_score >= buy_score * 0.75:
        return True
    if sell_count >= 2 and buy_count <= 1 and sell_score > 0:
        return True
    return False


def bearish_regime_reason() -> str:
    return "Bearish regime: BUY blocked (elevated sell/exit pressure)."
