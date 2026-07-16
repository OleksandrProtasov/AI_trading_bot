"""Tests for finalized structural SL/TP."""
from core.structure_levels import finalize_structure_levels
from core.smc_retest import StructureSetup
from core.smc_analysis import Zone


def _setup(side="long", inv=99.0, target=105.0):
    return StructureSetup(
        symbol="TESTUSDT",
        side=side,
        state="ready",
        trend="up" if side == "long" else "down",
        sweep_price=99.5,
        sweep_index=10,
        bos_price=101.0,
        zone=Zone("ob", 100.0, 100.5, 100.25),
        invalidation=inv,
        target_price=target,
        created_ts=1,
        checklist={"retest": True},
    )


def test_widens_tight_structural_sl():
    s = _setup(inv=99.8, target=104.0)
    fl = finalize_structure_levels(
        s, entry=100.0, action="BUY", min_rr=3.0, min_sl_pct=0.55, volatility_pct=0.8
    )
    assert fl is not None
    assert fl.sl_pct >= 0.55 - 1e-9
    assert fl.widened_sl is True
    assert fl.rr_ratio >= 3.0
