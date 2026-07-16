"""Tests for liquidity-based TP selection."""
from core.liquidity_targets import (
    LiquidityLevel,
    build_liquidity_levels,
    select_tp_at_liquidity,
)


def test_select_nearest_liquidity_with_min_rr():
    entry, sl = 100.0, 99.0
    levels = [
        LiquidityLevel(101.5, "ltf_swing"),
        LiquidityLevel(103.0, "stop_cluster"),
        LiquidityLevel(108.0, "htf_swing"),
    ]
    picked = select_tp_at_liquidity(entry, sl, "long", levels, min_rr=2.0, min_tp_pct=1.0)
    assert picked is not None
    tp, kind, rr = picked
    assert tp == 103.0
    assert kind == "stop_cluster"
    assert rr >= 2.0


def test_skips_liquidity_below_min_rr():
    entry, sl = 100.0, 99.0
    levels = [LiquidityLevel(101.2, "ltf_swing")]
    assert select_tp_at_liquidity(entry, sl, "long", levels, min_rr=3.0) is None


def test_build_levels_prefers_nearest_swing():
    class S:
        def __init__(self, kind, price):
            self.kind = kind
            self.price = price

    swings = [S("high", 101.0), S("high", 105.0), S("low", 98.0)]
    levels = build_liquidity_levels(
        side="long",
        entry=100.0,
        structural_target=104.0,
        ltf_swings=swings,
    )
    kinds = {lv.kind for lv in levels}
    assert "ltf_swing" in kinds
    assert "structural" in kinds
