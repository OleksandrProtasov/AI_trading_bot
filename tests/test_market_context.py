"""Tests for market context scoring and gates."""
from core.market_context import (
    BookMetrics,
    OiMetrics,
    analyze_orderbook,
    build_market_context,
    compute_potential_metrics,
    enrich_score_with_market_context,
)
from core.setup_scoring import SetupScore


class _FakeSetup:
    def __init__(self):
        self.side = "long"
        self.bos_price = 102.0
        self.sweep_price = 99.5
        self.zone = type("Z", (), {"kind": "OB", "low": 100.0, "high": 101.0})()


def test_analyze_orderbook_buy_aligned():
    bids = [[100.0, 10.0], [99.9, 10.0]]
    asks = [[100.1, 1.0], [100.2, 1.0]]
    m = analyze_orderbook(bids, asks, "BUY", min_depth_usd=100.0, min_imbalance=0.08)
    assert m.book_aligned is True
    assert m.thin_book is False


def test_potential_rejects_tight_sl():
    setup = _FakeSetup()
    p = compute_potential_metrics(
        setup, entry=100.0, sl=99.8, tp=101.5, action="BUY", min_sl_pct=0.45
    )
    assert p.sl_distance_pct < 0.45
    assert p.potential_ok is False


def test_oi_divergence_blocks_gate():
    setup = _FakeSetup()
    ctx = build_market_context(
        action="BUY",
        setup=setup,
        entry=100.5,
        sl=99.0,
        tp=105.0,
        entry_mode="retest",
        book_metrics=BookMetrics(book_aligned=True, thin_book=False),
        oi_metrics=OiMetrics(available=True, change_pct=-3.0, oi_divergence=True),
    )
    assert ctx.gate_ok is False
    assert "OI" in ctx.gate_reason


def test_enrich_soft_penalty_on_gate_fail():
    sc = SetupScore(
        quality_score=85,
        phase="ready",
        win_probability=0.55,
        aligned_action="BUY",
        sl=99.0,
        tp=105.0,
        entry_mode="retest",
        checklist={},
        components={},
    )
    setup = _FakeSetup()
    out = enrich_score_with_market_context(
        sc,
        setup=setup,
        entry_price=100.2,
        book_metrics=BookMetrics(thin_book=True),
    )
    assert out.phase == "ready"
    assert out.checklist.get("market_gate_ok") is False
