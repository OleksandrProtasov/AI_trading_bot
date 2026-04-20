"""Expert council merge logic (no network)."""
from core.event_router import Priority, Signal
from core.expert_council import expert_risk_officer, merge_expert_votes, refine_aggregate
from agents.aggregator_agent import Action, AggregatedSignal, RiskLevel


def _sig(agent: str, stype: str, pri=Priority.MEDIUM, **data):
    return Signal(agent, stype, pri, "m", symbol="BTCUSDT", data=data or {})


def test_merge_prefers_weighted_plurality():
    votes = [
        type("V", (), {"expert_id": "a", "action": "BUY", "confidence": 0.8, "note": "", "weight": 1.0})(),
        type("V", (), {"expert_id": "b", "action": "BUY", "confidence": 0.7, "note": "", "weight": 1.0})(),
        type("V", (), {"expert_id": "c", "action": "WAIT", "confidence": 0.5, "note": "", "weight": 1.0})(),
    ]
    winner, conf, dis, _notes = merge_expert_votes(votes)
    assert winner == "BUY"
    assert conf > 0
    assert 0 <= dis <= 1


def test_risk_officer_escalates_on_critical_emergency():
    sigs = [_sig("emergency", "price_spike", Priority.CRITICAL)]
    v = expert_risk_officer("BUY", 0.7, sigs)
    assert v.action == "EXIT"


def test_refine_aggregate_runs():
    agg = AggregatedSignal(
        "BTCUSDT",
        Action.BUY,
        RiskLevel.MEDIUM,
        0.8,
        ["base"],
        price=1.0,
    )
    sigs = [_sig("market", "volume_spike", Priority.HIGH)]
    refine_aggregate(agg, sigs, None, enabled=True)
    assert agg.action in (Action.BUY, Action.WAIT, Action.EXIT, Action.SELL)
    assert 0 <= agg.confidence <= 1
    assert any("Council" in r for r in agg.reasons)
