"""Research report files: promotion rules and .env merge helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_paths import repo_root

EV_ENV_KEYS = (
    "AGG_EV_GATE_ENABLED",
    "AGG_EV_FEE_BPS_PER_SIDE",
    "AGG_EV_SLIPPAGE_BPS",
    "AGG_EV_BUFFER_BPS",
    "AGG_EV_CONFIDENCE_MULT",
    "AGG_EV_MARGIN_MULT",
    "AGG_EV_SOURCE_MULT",
    "AGG_EV_BEARISH_PENALTY_MULT",
    "AGG_EV_EMERGENCY_PENALTY_MULT",
    "AGG_EV_CONFLICT_PENALTY_MULT",
)

PARAM_TO_ENV = {
    "ev_buffer_bps": "AGG_EV_BUFFER_BPS",
    "ev_confidence_mult": "AGG_EV_CONFIDENCE_MULT",
    "ev_margin_mult": "AGG_EV_MARGIN_MULT",
    "ev_source_mult": "AGG_EV_SOURCE_MULT",
    "ev_bearish_penalty_mult": "AGG_EV_BEARISH_PENALTY_MULT",
    "ev_emergency_penalty_mult": "AGG_EV_EMERGENCY_PENALTY_MULT",
    "ev_conflict_penalty_mult": "AGG_EV_CONFLICT_PENALTY_MULT",
}


def reports_dir() -> Path:
    return repo_root() / "reports"


def should_promote(
    *,
    current: Dict[str, float],
    previous: Optional[Dict[str, float]],
    min_score_delta: float,
    max_drawdown_pct: float,
    promote_on_equal: bool,
) -> bool:
    if current.get("avg_drawdown_pct", 0.0) > max_drawdown_pct:
        return False
    if previous is None:
        return True
    delta = current.get("score", -1e9) - previous.get("score", -1e9)
    if promote_on_equal:
        return delta >= 0.0
    return delta >= min_score_delta


def params_to_env_lines(params: Dict[str, Any]) -> List[str]:
    lines = [
        "# Auto-generated research EV parameters",
    ]
    for src_key, env_key in PARAM_TO_ENV.items():
        if src_key in params:
            lines.append(f"{env_key}={params[src_key]}")
    return lines


def parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def env_updates_from_params(params: Dict[str, Any]) -> Dict[str, str]:
    return {
        env_key: str(params[src_key])
        for src_key, env_key in PARAM_TO_ENV.items()
        if src_key in params
    }


def merge_env_file(
    target_path: Path,
    updates: Dict[str, str],
    *,
    allowed_keys: Optional[Tuple[str, ...]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Merge key=value updates into an env file.
    Returns (updated_keys, appended_keys).
    """
    allowed = set(allowed_keys or EV_ENV_KEYS)
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return [], []

    lines: List[str] = []
    if target_path.exists():
        lines = target_path.read_text(encoding="utf-8").splitlines()

    index_by_key: Dict[str, int] = {}
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        index_by_key[key] = i

    updated: List[str] = []
    appended: List[str] = []
    for key, value in filtered.items():
        new_line = f"{key}={value}"
        if key in index_by_key:
            lines[index_by_key[key]] = new_line
            updated.append(key)
        else:
            lines.append(new_line)
            appended.append(key)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    target_path.write_text(text, encoding="utf-8")
    return updated, appended


def load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_history(path: Path, tail: Optional[int] = None) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        if tail is None:
            return data
        n = max(1, int(tail))
        return data[-n:]
    except Exception:
        return []


def load_research_summary(
    *,
    history_path: Optional[Path] = None,
    latest_wf_path: Optional[Path] = None,
    promoted_env_path: Optional[Path] = None,
    best_env_path: Optional[Path] = None,
    history_tail: int = 10,
) -> Dict[str, Any]:
    base = reports_dir()
    history_path = history_path or (base / "daily_research_history.json")
    latest_wf_path = latest_wf_path or (base / "latest_wf.json")
    promoted_env_path = promoted_env_path or (base / "promoted_best.env")
    best_env_path = best_env_path or (base / "best_params.env")

    history = load_history(history_path, tail=history_tail)
    latest = history[-1] if history else None
    previous = history[-2] if len(history) >= 2 else None

    return {
        "paths": {
            "history": str(history_path),
            "latest_wf": str(latest_wf_path),
            "promoted_env": str(promoted_env_path),
            "best_env": str(best_env_path),
        },
        "latest_run": latest,
        "previous_run": previous,
        "latest_wf": load_json_file(latest_wf_path),
        "promoted_env": parse_env_file(promoted_env_path),
        "best_env": parse_env_file(best_env_path),
        "history_tail": history,
    }
