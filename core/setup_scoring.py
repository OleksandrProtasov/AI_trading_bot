"""Quality scoring for SMC setups (0–100) + estimated win probability."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.smc_analysis import is_mid_range
from core.smc_retest import RetestConfig, setup_allows_action
from core.structure_levels import finalize_structure_levels


@dataclass
class SetupScore:
    quality_score: int = 0
    phase: str = "none"  # none | forming | ready
    win_probability: float = 0.0
    aligned_action: str = ""
    sl: Optional[float] = None
    tp: Optional[float] = None
    rr_ratio: float = 0.0
    reason: str = ""
    one_liner: str = ""
    entry_mode: str = "none"  # none | retest | continuation
    checklist: Dict[str, Any] = field(default_factory=dict)
    components: Dict[str, int] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.phase == "ready" and self.quality_score > 0

    @property
    def is_forming(self) -> bool:
        return self.phase == "forming"


def compute_setup_score(
    *,
    action: str,
    trend: str,
    range_pos: float,
    setup: Any,
    cfg: RetestConfig,
    entry_price: Optional[float],
    aggregator_confidence: float = 0.0,
    ml_win_prob: float = 0.0,
    ev_passed: bool = True,
) -> SetupScore:
    act = (action or "").upper()
    components: Dict[str, int] = {}
    score = 0

    if act == "BUY":
        if trend == "up":
            components["trend"] = 20
        elif trend == "range":
            components["trend"] = 10
        else:
            components["trend"] = -20
    elif act == "SELL":
        if trend == "down":
            components["trend"] = 20
        elif trend == "range":
            components["trend"] = 10
        else:
            components["trend"] = -20
    else:
        components["trend"] = 0
    score += components["trend"]

    if not is_mid_range(range_pos, block_mid_pct=cfg.block_mid_range_pct):
        edge = 10 if (range_pos <= 0.32 or range_pos >= 0.68) else 6
        components["position"] = edge
        score += edge
    else:
        components["position"] = -12
        score -= 12

    checklist: Dict[str, Any] = {
        "htf_trend": trend,
        "range_position": round(range_pos, 3),
        "setup_state": setup.state if setup else "none",
        "liquidity_sweep": bool(setup and setup.checklist.get("liquidity_sweep")),
        "bos": bool(setup and setup.checklist.get("bos")),
        "retest": bool(setup and setup.checklist.get("retest")),
        "continuation": bool(setup and setup.checklist.get("continuation")),
    }

    if setup and setup.checklist.get("liquidity_sweep"):
        components["sweep"] = 15
        score += 15
    if setup and setup.checklist.get("bos"):
        components["bos"] = 20
        score += 20
    if setup:
        if setup.state == "await_retest":
            components["retest"] = 14
            score += 14
        elif setup.state == "ready":
            if setup.checklist.get("continuation"):
                components["continuation"] = 22
                score += 22
            elif setup.checklist.get("retest"):
                components["retest"] = 25
                score += 25
            else:
                components["retest"] = 20
                score += 20
        elif setup.state == "await_bos":
            components["retest"] = 6
            score += 6

    agent_pts = int(min(12, max(0.0, aggregator_confidence) * 12))
    components["agents"] = agent_pts
    score += agent_pts

    if ml_win_prob > 0:
        ml_pts = int(min(8, ml_win_prob * 10))
        components["ml"] = ml_pts
        score += ml_pts

    if not ev_passed:
        components["ev"] = -15
        score -= 15
    else:
        components["ev"] = 5
        score += 5

    sl = tp = None
    rr = 0.0
    entry = float(entry_price or 0.0)
    entry_mode = "none"
    if setup and entry > 0 and setup.state == "ready" and setup_allows_action(setup, act):
        if setup.checklist.get("continuation"):
            entry_mode = "continuation"
        elif setup.checklist.get("retest"):
            entry_mode = "retest"
        from core.strategy_engine import propose_entry

        ent, in_zone, _ = propose_entry(setup, act, entry, entry_mode)
        if in_zone and ent > 0:
            entry = ent
        fl = finalize_structure_levels(
            setup,
            entry,
            act,
            min_rr=cfg.min_rr,
            min_sl_pct=0.55,
            max_sl_pct=2.5,
            min_tp_pct=1.2,
            max_tp_pct=8.0,
        )
        if fl:
            sl, tp, rr = fl.sl, fl.tp, fl.rr_ratio
        if rr >= cfg.min_rr:
            components["rr"] = 10
            score += 10
        elif rr >= 2.0:
            components["rr"] = 5
            score += 5
        else:
            components["rr"] = -8
            score -= 8

    quality = max(0, min(100, score))

    if setup and setup.state == "ready" and setup_allows_action(setup, act) and rr >= 2.0:
        phase = "ready"
    elif setup and setup.state in ("await_bos", "await_retest"):
        phase = "forming"
    elif quality >= 50 and setup:
        phase = "forming"
    else:
        phase = "none"

    base_p = quality / 100.0
    if ml_win_prob > 0:
        win_p = 0.5 * base_p + 0.5 * ml_win_prob
    else:
        win_p = base_p * 0.72
    if phase == "ready":
        win_p = min(0.92, win_p + 0.05)
    elif phase == "forming":
        win_p *= 0.85
    win_p = max(0.05, min(0.90, win_p))

    one_liner = ""
    reason = ""
    if setup and phase == "ready":
        if entry_mode == "none":
            entry_mode = (
                "continuation" if setup.checklist.get("continuation") else "retest"
            )
        if entry_mode == "continuation":
            one_liner = (
                f"{trend.upper()} {setup.side}: sweep→BOS→продолжение без retest, "
                f"RR 1:{rr:.1f}"
            )
        else:
            entry_mode = "retest"
            one_liner = (
                f"{trend.upper()} {setup.side}: sweep→BOS→retest {setup.zone.kind}, "
                f"RR 1:{rr:.1f}"
            )
        reason = one_liner
    elif setup and phase == "forming":
        if setup.state == "await_bos":
            reason = "Свип ликвидности — ждём BOS"
        elif setup.state == "await_retest":
            z = setup.zone
            reason = f"BOS есть — ждём retest {z.kind} [{z.low:.4g}–{z.high:.4g}]"
        else:
            reason = "Сетап формируется"
    elif quality < 50:
        reason = "Низкое качество — пропуск"
    else:
        reason = "Недостаточно подтверждений SMC"

    return SetupScore(
        quality_score=quality,
        phase=phase,
        win_probability=win_p,
        aligned_action=act,
        sl=sl,
        tp=tp,
        rr_ratio=rr,
        reason=reason,
        one_liner=one_liner,
        entry_mode=entry_mode,
        checklist=checklist,
        components=components,
    )
