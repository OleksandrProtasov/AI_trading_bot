"""Tests for setup quality scoring."""
from core.setup_scoring import compute_setup_score
from core.smc_retest import RetestConfig


class _FakeSetup:
    def __init__(self, state, side="long"):
        self.state = state
        self.side = side
        self.zone = type("Z", (), {"kind": "OB", "low": 100.0, "high": 101.0})()
        self.invalidation = 99.0 if side == "long" else 102.0
        self.target_price = 105.0 if side == "long" else 95.0
        self.checklist = {
            "liquidity_sweep": state != "none",
            "bos": state in ("await_retest", "ready"),
            "retest": state == "ready",
        }


def test_ready_setup_scores_high():
    cfg = RetestConfig()
    setup = _FakeSetup("ready", "long")
    sc = compute_setup_score(
        action="BUY",
        trend="up",
        range_pos=0.2,
        setup=setup,
        cfg=cfg,
        entry_price=100.5,
        aggregator_confidence=0.75,
        ml_win_prob=0.55,
        ev_passed=True,
    )
    assert sc.quality_score >= 70
    assert sc.phase == "ready"
    assert sc.win_probability >= 0.45


def test_forming_setup_lower_score():
    cfg = RetestConfig()
    setup = _FakeSetup("await_retest", "short")
    sc = compute_setup_score(
        action="SELL",
        trend="down",
        range_pos=0.8,
        setup=setup,
        cfg=cfg,
        entry_price=50.0,
        aggregator_confidence=0.6,
    )
    assert sc.phase == "forming"
    assert sc.quality_score >= 40
