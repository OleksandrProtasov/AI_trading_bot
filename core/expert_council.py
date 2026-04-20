"""
Multi-"expert" deliberation layer (rule-based by default).

This is **not** online supervised ML: experts are deterministic heuristics with
different risk profiles. They vote on the baseline action from the aggregator;
high disagreement reduces confidence or nudges toward WAIT.

You can later replace an expert with a trained model behind the same vote API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core.event_router import Priority, Signal


@dataclass
class ExpertVote:
    expert_id: str
    action: str
    confidence: float
    note: str
    weight: float = 1.0


def _has_critical_emergency(signals: List[Signal]) -> bool:
    return any(
        s.agent_type == "emergency" and s.priority == Priority.CRITICAL for s in signals
    )


def _liquidity_conflict(signals: List[Signal]) -> bool:
    """Bearish book bias while others scream long — crude conflict proxy."""
    for s in signals:
        if s.agent_type != "liquidity" or s.signal_type != "orderbook_imbalance":
            continue
        imb = (s.data or {}).get("imbalance")
        try:
            if imb is not None and float(imb) < -0.25:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _momentum_support(signals: List[Signal]) -> float:
    """0..1 rough momentum score from recent signal types."""
    score = 0.0
    for s in signals:
        st = s.signal_type.lower()
        if "pump" in st or "break" in st or "volume_spike" in st:
            score += 0.15
        if s.agent_type == "emergency" and "price_spike" in st:
            score += 0.2
    return min(score, 1.0)


def expert_risk_officer(
    baseline_action: str, baseline_conf: float, signals: List[Signal]
) -> ExpertVote:
    if _has_critical_emergency(signals):
        return ExpertVote(
            expert_id="risk_officer",
            action="EXIT",
            confidence=min(1.0, baseline_conf + 0.15),
            note="Critical emergency present — de-risk first.",
            weight=1.2,
        )
    if baseline_action == "BUY" and baseline_conf < 0.62:
        return ExpertVote(
            expert_id="risk_officer",
            action="WAIT",
            confidence=0.45,
            note="Buy edge too weak for conservative profile.",
            weight=1.0,
        )
    return ExpertVote(
        expert_id="risk_officer",
        action=baseline_action,
        confidence=baseline_conf * 0.92,
        note="Baseline accepted with slight discount.",
        weight=1.0,
    )


def expert_momentum_trader(
    baseline_action: str, baseline_conf: float, signals: List[Signal]
) -> ExpertVote:
    mom = _momentum_support(signals)
    if baseline_action in ("BUY", "WAIT") and mom > 0.35:
        conf = min(1.0, max(baseline_conf, 0.55) + 0.1 * mom)
        return ExpertVote(
            expert_id="momentum_trader",
            action="BUY",
            confidence=conf,
            note="Momentum cluster supports long bias.",
            weight=0.9,
        )
    if baseline_action == "EXIT":
        return ExpertVote(
            expert_id="momentum_trader",
            action="EXIT",
            confidence=min(1.0, baseline_conf + 0.05),
            note="Honours emergency / exit consensus.",
            weight=1.0,
        )
    return ExpertVote(
        expert_id="momentum_trader",
        action=baseline_action,
        confidence=baseline_conf,
        note="No strong momentum override.",
        weight=0.9,
    )


def expert_flow_skeptic(
    baseline_action: str, baseline_conf: float, signals: List[Signal]
) -> ExpertVote:
    if baseline_action == "BUY" and _liquidity_conflict(signals):
        return ExpertVote(
            expert_id="flow_skeptic",
            action="WAIT",
            confidence=min(baseline_conf, 0.55),
            note="Book flow conflicts with long narrative.",
            weight=1.1,
        )
    if baseline_action == "SELL":
        return ExpertVote(
            expert_id="flow_skeptic",
            action="SELL",
            confidence=baseline_conf * 1.05,
            note="Aligns with defensive flow read.",
            weight=1.0,
        )
    return ExpertVote(
        expert_id="flow_skeptic",
        action=baseline_action,
        confidence=baseline_conf * 0.95,
        note="No strong flow contradiction.",
        weight=1.0,
    )


def merge_expert_votes(votes: List[ExpertVote]) -> Tuple[str, float, float, List[str]]:
    """
    Weighted plurality. Returns (action, confidence, disagreement, notes).

    disagreement in [0,1]: higher means experts diverged more.
    """
    weights_by_action: Dict[str, float] = {}
    conf_by_action: Dict[str, List[float]] = {}
    notes: List[str] = []

    for v in votes:
        w = v.weight * max(v.confidence, 0.01)
        weights_by_action[v.action] = weights_by_action.get(v.action, 0.0) + w
        conf_by_action.setdefault(v.action, []).append(v.confidence)
        notes.append(f"{v.expert_id}: {v.action} ({v.confidence:.2f}) — {v.note}")

    total = sum(weights_by_action.values()) or 1.0
    winner = max(weights_by_action, key=weights_by_action.get)
    top = weights_by_action[winner]
    disagreement = 1.0 - (top / total)
    merged_conf = sum(conf_by_action[winner]) / len(conf_by_action[winner])
    return winner, merged_conf, disagreement, notes


def refine_aggregate(
    aggregated: Any,
    signals: List[Signal],
    logger: Optional[Any],
    *,
    enabled: bool = True,
    disagreement_threshold: float = 0.45,
    disagreement_penalty: float = 0.35,
) -> None:
    """
    Mutate `aggregated` in place (expects aggregator.AggregatedSignal duck-type).

    When experts disagree strongly, confidence is pulled down; extreme cases
    downgrade BUY -> WAIT to avoid overconfident calls.
    """
    if not enabled or not signals:
        return

    from agents.aggregator_agent import Action

    baseline_action = aggregated.action.value
    baseline_conf = float(aggregated.confidence or 0.0)

    votes = [
        expert_risk_officer(baseline_action, baseline_conf, signals),
        expert_momentum_trader(baseline_action, baseline_conf, signals),
        expert_flow_skeptic(baseline_action, baseline_conf, signals),
    ]

    winner, merged_conf, disagreement, council_notes = merge_expert_votes(votes)

    if disagreement >= disagreement_threshold:
        merged_conf *= 1.0 - disagreement_penalty * disagreement
        if winner == "BUY" and baseline_action != "EXIT":
            # split vote on a long — stand aside unless baseline was already weak
            if merged_conf < 0.55 or disagreement > 0.55:
                winner = "WAIT"
                merged_conf = min(merged_conf, 0.5)

    if winner != baseline_action and logger:
        logger.info(
            "Council override %s -> %s (disagreement=%.2f)",
            baseline_action,
            winner,
            disagreement,
        )

    aggregated.action = Action(winner)
    aggregated.confidence = max(0.0, min(1.0, merged_conf))
    aggregated.reasons = (aggregated.reasons or []) + [
        f"Council (d={disagreement:.2f}): {winner}"
    ]
    aggregated.reasons.extend(council_notes[:3])
