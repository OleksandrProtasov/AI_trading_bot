from core.smc_analysis import (
    detect_liquidity_sweep,
    find_swings,
    structural_sl_tp,
)
from core.smc_retest import StructureSetupStore, RetestConfig, detect_retest_touch, detect_continuation_hold
from core.smc_analysis import Zone


def _c(ts, o, h, l, c):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_liquidity_sweep_long():
    candles = []
    t = 1_000_000
    for i in range(20):
        candles.append(_c(t + i * 60, 100.0, 100.1, 99.9, 100.0))
    candles.append(_c(t + 20 * 60, 100.0, 100.1, 96.0, 100.05))
    swings = find_swings(candles, wing=2)
    ev = detect_liquidity_sweep(candles, swings, side="long", lookback_bars=3)
    assert ev is not None
    assert ev.event == "sweep"


def test_retest_touch_long():
    zone = Zone("ob", 99.0, 99.5, 99.25)
    candles = [_c(1, 99.2, 99.55, 99.05, 99.45)]
    assert detect_retest_touch(candles, side="long", zone=zone, invalidation=98.5)


def test_structural_sl_tp_long():
    sl, tp, rr = structural_sl_tp(
        100.0,
        "long",
        invalidation=98.0,
        target=105.0,
        min_rr=3.0,
    )
    assert sl < 100 < tp
    assert rr >= 3.0


def test_continuation_hold_long_without_retest():
    zone = Zone("ob", 99.0, 99.5, 99.25)
    candles = []
    t = 1_000_000
    # BOS bar at index 5, close 100.5
    for i in range(12):
        o, c = 100.0 + i * 0.1, 100.2 + i * 0.15
        candles.append(_c(t + i * 60, o, c + 0.2, c - 0.1, c))
    assert detect_continuation_hold(
        candles,
        side="long",
        zone=zone,
        bos_price=100.0,
        bos_index=5,
        invalidation=98.5,
        min_hold_bars=3,
        min_wait_bars=4,
        min_displacement_pct=0.1,
    )


def test_continuation_rejected_if_zone_touched():
    zone = Zone("ob", 99.0, 99.5, 99.25)
    candles = [
        _c(1, 100.0, 100.5, 99.2, 100.3),
        _c(2, 100.3, 100.6, 99.4, 100.4),
    ]
    assert not detect_continuation_hold(
        candles,
        side="long",
        zone=zone,
        bos_price=100.0,
        bos_index=0,
        invalidation=98.5,
        min_hold_bars=1,
        min_wait_bars=1,
        min_displacement_pct=0.01,
    )


def test_setup_store_ready_state():
    import time

    cfg = RetestConfig(require_htf_trend=False, block_range_trend=False, block_mid_range_pct=0.0)
    store = StructureSetupStore(cfg)
    from core.smc_retest import StructureSetup

    store._setups["TEST"] = StructureSetup(
        symbol="TEST",
        side="long",
        state="ready",
        trend="up",
        sweep_price=97.0,
        sweep_index=10,
        bos_price=99.0,
        zone=Zone("ob", 99.0, 99.5, 99.25),
        invalidation=96.5,
        target_price=102.0,
        created_ts=int(time.time()),
        checklist={"retest": True, "bos": True, "liquidity_sweep": True},
    )
    assert store.get("TEST") is not None
    assert store.get("TEST").state == "ready"
