"""Load/derive per-agent score multipliers from edge statistics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from core.agent_edge import compute_agent_edge
from core.runtime_paths import repo_root

DEFAULT_WEIGHTS_PATH = repo_root() / "reports" / "agent_weights.json"


def derive_weights_from_edge(edge_report: Dict[str, Any]) -> Dict[str, float]:
    """
    Map agent edge rows to multipliers in [0.2, 1.2].
    Negative avg return or weak hit-rate -> downweight.
    """
    out: Dict[str, float] = {}
    for row in edge_report.get("by_agent") or []:
        agent = str(row.get("agent_type") or "").lower()
        if not agent or agent == "unknown":
            out[agent or "unknown"] = 0.35
            continue
        avg_ret = float(row.get("avg_return_pct") or 0.0)
        hit = float(row.get("hit_rate") or 0.0)
        mult = 1.0 + max(-0.5, min(0.5, avg_ret / 20.0))
        if hit < 0.35:
            mult *= 0.65
        elif hit > 0.48 and avg_ret > 0:
            mult = min(1.2, mult * 1.05)
        if agent == "shitcoin":
            mult = min(mult, 0.55)
        out[agent] = max(0.2, min(1.2, round(mult, 3)))
    return out


def build_agent_weights(
    db_path: str,
    *,
    since_ts: int,
    lookback_sec: int = 180,
) -> Dict[str, Any]:
    edge = compute_agent_edge(db_path, since_ts=since_ts, lookback_sec=lookback_sec)
    weights = derive_weights_from_edge(edge)
    return {
        "since_ts": since_ts,
        "lookback_sec": lookback_sec,
        "edge_outcomes": edge.get("outcomes", 0),
        "weights": weights,
    }


def save_agent_weights(payload: Dict[str, Any], path: Path | None = None) -> Path:
    path = path or DEFAULT_WEIGHTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_agent_weights(path: Path | None = None) -> Dict[str, float]:
    path = path or DEFAULT_WEIGHTS_PATH
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        weights = data.get("weights") if isinstance(data, dict) else data
        if not isinstance(weights, dict):
            return {}
        return {str(k).lower(): float(v) for k, v in weights.items()}
    except Exception:
        return {}
