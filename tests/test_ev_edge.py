from core.ev_edge import evaluate_edge_gate, required_edge_bps


def test_required_edge_includes_min_profit_when_rr_on():
    required, cost, profit = required_edge_bps(
        fee_bps_per_side=2.0,
        slippage_bps=3.0,
        buffer_bps=6.0,
        min_profit_bps=15.0,
        rr_gate_enabled=True,
    )
    assert cost == 13.0
    assert profit == 15.0
    assert required == 28.0


def test_rr_gate_blocks_weak_edge():
    res = evaluate_edge_gate(
        action="BUY",
        confidence=0.55,
        margin=0.05,
        source_count=2,
        bearish_pressure=0,
        emergency_count=0,
        buy_count=2,
        sell_count=0,
        fee_bps_per_side=2.0,
        slippage_bps=3.0,
        buffer_bps=6.0,
        min_profit_bps=15.0,
        ev_gate_enabled=True,
        rr_gate_enabled=True,
    )
    assert res.passed is False
    assert "Edge gate" in res.reason


def test_rr_gate_disabled_uses_cost_floor_only():
    required, _, profit = required_edge_bps(
        fee_bps_per_side=2.0,
        slippage_bps=3.0,
        buffer_bps=6.0,
        min_profit_bps=15.0,
        rr_gate_enabled=False,
    )
    assert required == 13.0
    assert profit == 0.0
