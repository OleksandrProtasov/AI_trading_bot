"""Expected edge (bps) and R:R gate for directional entries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EdgeGateResult:
    passed: bool
    edge_bps: float
    required_bps: float
    cost_floor_bps: float
    min_profit_bps: float
    calibration_extra_bps: float = 0.0
    reason: str = ""


def expected_edge_bps(
    *,
    action: str = "BUY",
    confidence: float,
    margin: float,
    source_count: int,
    bearish_pressure: int,
    emergency_count: int,
    buy_count: int,
    sell_count: int,
    confidence_mult: float = 16.0,
    margin_mult: float = 20.0,
    source_mult: float = 3.0,
    bearish_penalty_mult: float = 6.0,
    emergency_penalty_mult: float = 4.0,
    conflict_penalty_mult: float = 25.0,
) -> float:
    conf_term = max(0.0, confidence - 0.5) * confidence_mult
    margin_term = max(0.0, margin) * margin_mult
    source_term = max(0, source_count - 1) * source_mult
    conflict_penalty = (
        float(min(buy_count, sell_count)) / float(max(1, buy_count + sell_count))
    ) * conflict_penalty_mult

    act = (action or "").upper()
    if act == "SELL":
        bearish_term = max(0, bearish_pressure) * (bearish_penalty_mult * 0.5)
        emergency_term = max(0, emergency_count) * (emergency_penalty_mult * 0.25)
    else:
        bearish_term = -max(0, bearish_pressure) * bearish_penalty_mult
        emergency_term = -max(0, emergency_count) * emergency_penalty_mult

    return conf_term + margin_term + source_term + bearish_term + emergency_term - conflict_penalty


def required_edge_bps(
    *,
    fee_bps_per_side: float,
    slippage_bps: float,
    buffer_bps: float,
    min_profit_bps: float,
    rr_gate_enabled: bool,
) -> tuple[float, float, float]:
    """
    Returns (required_bps, cost_floor_bps, applied_min_profit_bps).
    R:R gate adds min_profit_bps on top of trading costs + safety buffer.
    """
    cost_floor = 2.0 * fee_bps_per_side + slippage_bps + buffer_bps
    profit_floor = float(min_profit_bps) if rr_gate_enabled else 0.0
    return cost_floor + profit_floor, cost_floor, profit_floor


def evaluate_edge_gate(
    *,
    action: str,
    confidence: float,
    margin: float,
    source_count: int,
    bearish_pressure: int,
    emergency_count: int,
    buy_count: int,
    sell_count: int,
    fee_bps_per_side: float,
    slippage_bps: float,
    buffer_bps: float,
    min_profit_bps: float,
    ev_gate_enabled: bool,
    rr_gate_enabled: bool,
    confidence_mult: float = 16.0,
    margin_mult: float = 20.0,
    source_mult: float = 3.0,
    bearish_penalty_mult: float = 6.0,
    emergency_penalty_mult: float = 4.0,
    conflict_penalty_mult: float = 25.0,
    calibration_extra_bps: float = 0.0,
) -> EdgeGateResult:
    act = (action or "").upper()
    if act not in ("BUY", "SELL"):
        return EdgeGateResult(True, 0.0, 0.0, 0.0, 0.0)
    if not ev_gate_enabled:
        return EdgeGateResult(True, 0.0, 0.0, 0.0, 0.0)

    edge = expected_edge_bps(
        action=act,
        confidence=confidence,
        margin=margin,
        source_count=source_count,
        bearish_pressure=bearish_pressure,
        emergency_count=emergency_count,
        buy_count=buy_count,
        sell_count=sell_count,
        confidence_mult=confidence_mult,
        margin_mult=margin_mult,
        source_mult=source_mult,
        bearish_penalty_mult=bearish_penalty_mult,
        emergency_penalty_mult=emergency_penalty_mult,
        conflict_penalty_mult=conflict_penalty_mult,
    )
    required, cost_floor, profit_floor = required_edge_bps(
        fee_bps_per_side=fee_bps_per_side,
        slippage_bps=slippage_bps,
        buffer_bps=buffer_bps,
        min_profit_bps=min_profit_bps,
        rr_gate_enabled=rr_gate_enabled,
    )
    cal_extra = max(0.0, float(calibration_extra_bps))
    required_total = required + cal_extra
    passed = edge >= required_total
    reason = ""
    if not passed:
        reason = (
            f"Edge gate: expected {edge:.1f}bps < required {required_total:.1f}bps "
            f"(costs {cost_floor:.1f} + profit {profit_floor:.1f}"
            + (f" + calibration {cal_extra:.1f}" if cal_extra > 0 else "")
            + ")."
        )
    return EdgeGateResult(
        passed=passed,
        edge_bps=edge,
        required_bps=required_total,
        cost_floor_bps=cost_floor,
        min_profit_bps=profit_floor,
        calibration_extra_bps=cal_extra,
        reason=reason,
    )
