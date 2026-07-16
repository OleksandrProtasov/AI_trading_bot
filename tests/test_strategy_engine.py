from core.market_context import BookMetrics
from core.order_flow import TradeFlowMetrics
from core.strategy_engine import evaluate_ready_strategy, propose_entry
from core.smc_retest import StructureSetup
from core.smc_analysis import Zone


def _setup(side="long"):
    return StructureSetup(
        symbol="TESTUSDT",
        side=side,
        state="ready",
        trend="up" if side == "long" else "down",
        sweep_price=99.0 if side == "long" else 101.5,
        sweep_index=10,
        bos_price=101.5 if side == "long" else 99.0,
        zone=Zone("ob", 100.0, 100.5, 100.25),
        invalidation=99.0 if side == "long" else 101.5,
        target_price=105.0 if side == "long" else 95.0,
        created_ts=1,
        checklist={"retest": True},
    )


def test_propose_entry_accepts_wick_touch():
    s = _setup()
    # Close above zone, but wick dipped into 100–100.5
    candles = [
        {"open": 101.2, "high": 101.4, "low": 100.2, "close": 101.3, "volume": 1},
    ]
    entry, ok, reason = propose_entry(
        s, "BUY", 101.3, "retest", recent_candles=candles, zone_tol_pct=0.75
    )
    assert ok is True
    assert "касание" in reason
    assert 100.0 <= entry <= 100.5


def test_propose_entry_rejects_price_outside_zone():
    s = _setup()
    entry, ok, reason = propose_entry(s, "BUY", 98.5, "retest")
    assert ok is False
    assert "вне зоны" in reason


def test_propose_entry_accepts_retest_in_zone():
    s = _setup()
    entry, ok, _ = propose_entry(s, "BUY", 100.2, "retest")
    assert ok is True
    assert 100.0 <= entry <= 100.5


def test_strategy_blocks_without_flow_alignment():
    s = _setup()
    flow = TradeFlowMetrics(
        delta_pct=-12.0,
        trade_count=20,
        flow_aligned=False,
        dominance="sellers",
    )
    book = BookMetrics(
        imbalance=0.15,
        book_aligned=True,
        bid_depth_usd=100_000,
        ask_depth_usd=80_000,
        spread_pct=0.05,
    )
    d = evaluate_ready_strategy(
        setup=s,
        action="BUY",
        current_price=100.2,
        entry_mode="retest",
        db_path=":memory:",
        symbol="TESTUSDT",
        book_metrics=book,
        flow_metrics=flow,
        require_book_aligned=True,
        require_flow_aligned=True,
    )
    assert d.ok is False
    assert "поток" in d.block_reason.lower()


def test_strategy_ok_with_aligned_flow_and_zone():
    s = _setup()
    flow = TradeFlowMetrics(
        delta_pct=15.0,
        trade_count=20,
        flow_aligned=True,
        dominance="buyers",
    )
    book = BookMetrics(
        imbalance=0.12,
        book_aligned=True,
        bid_depth_usd=120_000,
        ask_depth_usd=90_000,
        spread_pct=0.05,
    )
    d = evaluate_ready_strategy(
        setup=s,
        action="BUY",
        current_price=100.2,
        entry_mode="retest",
        db_path=":memory:",
        symbol="TESTUSDT",
        book_metrics=book,
        flow_metrics=flow,
        require_book_aligned=True,
        require_flow_aligned=True,
        min_rr=2.5,
    )
    assert d.ok is True
    assert d.in_zone is True
    assert d.sl > 0 and d.tp > d.entry
    assert d.exit_plan
    assert "покупатели" in d.dominance_label
