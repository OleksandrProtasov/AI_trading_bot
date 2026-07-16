"""Tests for partial exit logic."""
from core.partial_exit import (
    blend_position_pnl,
    check_live_hit,
    compute_tp1,
    resolve_tp1,
    should_use_partial_exit,
    simulate_partial_path,
)


def test_compute_tp1_long():
    tp1 = compute_tp1(100.0, 99.0, "BUY", partial_rr=1.5)
    assert abs(tp1 - 101.5) < 1e-9


def test_adaptive_skips_close_tp():
    # TP 2%, RR 2 — hold for full TP
    assert not should_use_partial_exit(100.0, 99.0, 102.0, "BUY", min_tp_pct=3.5, min_rr=2.8)
    assert resolve_tp1(100.0, 99.0, 102.0, "BUY") == 0.0


def test_adaptive_allows_far_tp():
    # TP 5%, RR 3
    assert should_use_partial_exit(100.0, 99.0, 105.0, "BUY", min_tp_pct=3.5, min_rr=2.8)
    assert resolve_tp1(100.0, 99.0, 105.0, "BUY") > 0


def test_partial_hit_before_full_tp():
    trade = {
        "entry": 100.0,
        "sl": 99.0,
        "tp": 104.0,
        "tp1": 101.5,
        "action": "BUY",
        "partial_taken": 0,
    }
    hit = check_live_hit(trade, 101.6, fee_bps_per_side=2.0)
    assert hit is not None
    assert hit.kind == "partial_tp"
    assert abs(hit.exit_price - 101.5) < 1e-9


def test_blend_pnl():
    blended = blend_position_pnl(1.5, 3.0, partial_size=0.5)
    assert abs(blended - 2.25) < 1e-9


def test_simulate_partial_on_candles():
    trade = {
        "entry": 100.0,
        "sl": 99.0,
        "tp": 105.0,
        "tp1": 101.5,
        "action": "BUY",
        "partial_taken": 0,
    }
    candles = [
        {"open": 100, "high": 100.5, "low": 99.8, "close": 100.2},
        {"open": 100.2, "high": 102.0, "low": 100.0, "close": 101.8},
    ]
    hit = simulate_partial_path(trade, candles, fee_bps_per_side=2.0)
    assert hit is not None
    assert hit.kind == "partial_tp"
