from core.trade_levels import (
    breakeven_win_rate,
    compute_trade_levels,
    passes_min_rr,
    simulate_sl_tp_path,
)


def test_compute_trade_levels_buy_rr3():
    lv = compute_trade_levels(100.0, "BUY", sl_pct=0.35, tp_rr_ratio=3.0)
    assert lv.sl == 99.65
    assert abs(lv.tp - 101.05) < 0.01
    assert abs(lv.rr_ratio - 3.0) < 0.01


def test_breakeven_win_rate_rr3():
    wr = breakeven_win_rate(3.0, fee_round_trip_pct=0.1)
    assert 0.25 < wr < 0.33


def test_simulate_tp_hit_buy():
    candles = [{"low": 99.9, "high": 101.2, "close": 101.0}]
    res = simulate_sl_tp_path(100.0, "BUY", candles, sl_price=99.5, tp_price=101.05)
    assert res is not None
    assert res.exit_reason == "tp"
    assert res.directional_hit == 1
    assert res.return_pct > 1.0


def test_simulate_sl_hit_buy():
    candles = [{"low": 99.5, "high": 100.2, "close": 99.7}]
    res = simulate_sl_tp_path(100.0, "BUY", candles, sl_price=99.65, tp_price=101.05)
    assert res is not None
    assert res.exit_reason == "sl"
    assert res.directional_hit == 0


def test_passes_min_rr():
    assert passes_min_rr(0.35, 1.05, min_rr=2.5)
    assert not passes_min_rr(0.35, 0.50, min_rr=2.5)
