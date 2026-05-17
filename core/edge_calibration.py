"""Calibrate edge gate thresholds from evaluated aggregated outcomes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.runtime_paths import repo_root

DEFAULT_CALIBRATION_PATH = repo_root() / "reports" / "edge_calibration.json"


def confidence_bucket(confidence: float) -> str:
    c = max(0.0, min(1.0, float(confidence)))
    if c < 0.60:
        return "0.50-0.60"
    if c < 0.70:
        return "0.60-0.70"
    if c < 0.80:
        return "0.70-0.80"
    return "0.80-1.00"


def bucket_key(action: str, confidence: float) -> str:
    return f"{(action or '').upper()}:{confidence_bucket(confidence)}"


def _extra_bps_from_avg_return(
    avg_return_pct: float,
    *,
    net_target_pct: float = 0.08,
    max_extra_bps: float = 30.0,
) -> float:
    """Penalty from weak/negative realized return (percent)."""
    if avg_return_pct < 0.0:
        return min(max_extra_bps, 8.0 + (-avg_return_pct) * 80.0)
    if avg_return_pct < net_target_pct:
        return min(max_extra_bps, (net_target_pct - avg_return_pct) * 40.0)
    return 0.0


def _extra_bps_from_hit_rate(
    hit_rate: float,
    avg_return_pct: float,
    *,
    min_hit_rate: float = 0.45,
    weak_return_pct: float = 0.05,
    max_extra_bps: float = 30.0,
) -> float:
    """
    Penalty when directional hit-rate is below target, especially with weak returns.
    hit_rate is 0..1 (not percent).
    """
    hr = max(0.0, min(1.0, float(hit_rate)))
    penalty = 0.0
    if hr < min_hit_rate:
        penalty += (min_hit_rate - hr) * 100.0
    if avg_return_pct < weak_return_pct and hr < min_hit_rate:
        penalty += (weak_return_pct - avg_return_pct) * 70.0
    return min(max_extra_bps, penalty)


def _combined_extra_bps(
    avg_return_pct: float,
    hit_rate: float,
    *,
    net_target_pct: float = 0.08,
    min_hit_rate: float = 0.45,
    weak_return_pct: float = 0.05,
    max_extra_bps: float = 30.0,
) -> Tuple[float, float, float]:
    ret_part = _extra_bps_from_avg_return(
        avg_return_pct,
        net_target_pct=net_target_pct,
        max_extra_bps=max_extra_bps,
    )
    hit_part = _extra_bps_from_hit_rate(
        hit_rate,
        avg_return_pct,
        min_hit_rate=min_hit_rate,
        weak_return_pct=weak_return_pct,
        max_extra_bps=max_extra_bps,
    )
    total = min(max_extra_bps, ret_part + hit_part)
    return total, ret_part, hit_part


def build_edge_calibration(
    db_path: str,
    *,
    since_ts: int,
    min_samples: int = 30,
    net_target_pct: float = 0.08,
    min_hit_rate: float = 0.45,
    weak_return_pct: float = 0.05,
    max_extra_bps: float = 30.0,
) -> Dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT action,
                   CASE
                     WHEN confidence < 0.60 THEN '0.50-0.60'
                     WHEN confidence < 0.70 THEN '0.60-0.70'
                     WHEN confidence < 0.80 THEN '0.70-0.80'
                     ELSE '0.80-1.00'
                   END AS bucket,
                   COUNT(*) AS n,
                   AVG(return_pct) AS avg_return_pct,
                   AVG(CASE WHEN directional_hit = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate
            FROM aggregated_outcomes
            WHERE evaluated_at IS NOT NULL
              AND evaluated_at >= ?
              AND return_pct IS NOT NULL
              AND action IN ('BUY', 'SELL')
            GROUP BY action, bucket
            """,
            (since_ts,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        action = str(row["action"]).upper()
        bucket = str(row["bucket"])
        key = f"{action}:{bucket}"
        n = int(row["n"] or 0)
        avg_ret = float(row["avg_return_pct"] or 0.0)
        hit = float(row["hit_rate"] or 0.0)
        extra = 0.0
        ret_part = 0.0
        hit_part = 0.0
        if n >= int(min_samples):
            extra, ret_part, hit_part = _combined_extra_bps(
                avg_ret,
                hit,
                net_target_pct=net_target_pct,
                min_hit_rate=min_hit_rate,
                weak_return_pct=weak_return_pct,
                max_extra_bps=max_extra_bps,
            )
        buckets[key] = {
            "action": action,
            "bucket": bucket,
            "n": n,
            "avg_return_pct": avg_ret,
            "hit_rate": hit,
            "return_penalty_bps": round(ret_part, 2),
            "hit_rate_penalty_bps": round(hit_part, 2),
            "extra_required_bps": round(extra, 2),
        }

    return {
        "built_at": datetime.utcnow().isoformat() + "Z",
        "since_ts": since_ts,
        "min_samples": min_samples,
        "net_target_pct": net_target_pct,
        "min_hit_rate": min_hit_rate,
        "weak_return_pct": weak_return_pct,
        "max_extra_bps": max_extra_bps,
        "buckets": buckets,
    }


def save_calibration(payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    path = path or DEFAULT_CALIBRATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_calibration(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or DEFAULT_CALIBRATION_PATH
    if not path.exists():
        return {"buckets": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"buckets": {}}
    except Exception:
        return {"buckets": {}}


def calibration_extra_bps(
    calibration: Dict[str, Any],
    *,
    action: str,
    confidence: float,
    enabled: bool = True,
) -> Tuple[float, str]:
    if not enabled:
        return 0.0, ""
    buckets = calibration.get("buckets") or {}
    key = bucket_key(action, confidence)
    entry = buckets.get(key) or {}
    extra = float(entry.get("extra_required_bps", 0.0) or 0.0)
    if extra <= 0:
        return 0.0, ""
    hit_pen = float(entry.get("hit_rate_penalty_bps", 0) or 0)
    ret_pen = float(entry.get("return_penalty_bps", 0) or 0)
    note = (
        f"Outcome calibration: {key} avg_ret={entry.get('avg_return_pct', 0):.3f}% "
        f"hit={float(entry.get('hit_rate', 0) or 0):.1%} "
        f"(+{ret_pen:.1f} ret, +{hit_pen:.1f} hit) -> +{extra:.1f}bps."
    )
    return extra, note
