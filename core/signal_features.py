"""Feature extraction for signal quality ML model."""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core.btc_trend import btc_trend_at_ts

FEATURE_NAMES = [
    "confidence",
    "is_buy",
    "is_sell",
    "risk_low",
    "risk_medium",
    "risk_high",
    "hour_sin",
    "hour_cos",
    "btc_return_30m",
    "btc_trend_down",
    "btc_trend_up",
    "symbol_vol_30m",
    "bearish_reason_hits",
    "source_reasons_count",
    "council_enabled",
    "council_changed",
    "unique_agents_est",
    "margin_est",
]


def _risk_one_hot(risk: str) -> Tuple[float, float, float]:
    r = (risk or "medium").lower()
    if r == "low":
        return 1.0, 0.0, 0.0
    if r == "high":
        return 0.0, 0.0, 1.0
    return 0.0, 1.0, 0.0


def _symbol_volatility_pct(
    conn: sqlite3.Connection,
    symbol: str,
    end_ts: int,
    *,
    lookback_minutes: int = 30,
) -> float:
    start_ts = int(end_ts) - int(lookback_minutes) * 60
    rows = conn.execute(
        """
        SELECT close FROM candles
        WHERE symbol = ? AND timeframe = '1m'
          AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
        """,
        (symbol.upper(), start_ts, int(end_ts)),
    ).fetchall()
    closes = [float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0]
    if len(closes) < 3:
        return 0.0
    rets = []
    for i in range(1, len(closes)):
        rets.append((closes[i] - closes[i - 1]) / closes[i - 1] * 100.0)
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    return math.sqrt(max(0.0, var))


def build_feature_row(
    *,
    db_path: str,
    signal_ts: int,
    symbol: str,
    action: str,
    confidence: float,
    risk: str,
    reasons: Optional[List[str]] = None,
    council_enabled: bool = True,
    council_changed: bool = False,
    source_signals_count: int = 0,
    margin_est: float = 0.0,
    unique_agents_est: int = 0,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, float]:
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(db_path)
    try:
        dt_hour = (int(signal_ts) % 86400) / 3600.0
        hour_rad = 2.0 * math.pi * dt_hour / 24.0
        btc = btc_trend_at_ts(db_path, int(signal_ts), lookback_minutes=30)
        btc_ret = float(btc.get("return_pct") or 0.0)
        trend = str(btc.get("trend") or "unknown")
        risk_low, risk_medium, risk_high = _risk_one_hot(risk)
        reason_text = " | ".join(str(r).lower() for r in (reasons or []))
        bearish_hits = sum(
            1
            for k in ("dump", "danger", "crisis", "sell", "support_break", "exit")
            if k in reason_text
        )
        act = (action or "").upper()
        sym_vol = _symbol_volatility_pct(conn, symbol, int(signal_ts))
        return {
            "confidence": float(confidence),
            "is_buy": 1.0 if act == "BUY" else 0.0,
            "is_sell": 1.0 if act == "SELL" else 0.0,
            "risk_low": risk_low,
            "risk_medium": risk_medium,
            "risk_high": risk_high,
            "hour_sin": math.sin(hour_rad),
            "hour_cos": math.cos(hour_rad),
            "btc_return_30m": btc_ret,
            "btc_trend_down": 1.0 if trend == "down" else 0.0,
            "btc_trend_up": 1.0 if trend == "up" else 0.0,
            "symbol_vol_30m": sym_vol,
            "bearish_reason_hits": float(bearish_hits),
            "source_reasons_count": float(len(reasons or [])),
            "council_enabled": 1.0 if council_enabled else 0.0,
            "council_changed": 1.0 if council_changed else 0.0,
            "unique_agents_est": float(unique_agents_est),
            "margin_est": float(margin_est),
        }
    finally:
        if own_conn and conn is not None:
            conn.close()


def row_to_vector(row: Dict[str, float]) -> List[float]:
    return [float(row.get(name, 0.0)) for name in FEATURE_NAMES]


def load_training_dataset(
    db_path: str,
    *,
    min_samples: int = 80,
) -> Tuple[List[List[float]], List[int], Dict[str, Any]]:
    """
    Build (X, y) from evaluated aggregated_outcomes.
    y=1 when directional_hit and return_pct > 0.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT signal_ts, symbol, action, confidence, risk, reasons_json,
                   council_enabled, council_changed, return_pct, directional_hit
            FROM aggregated_outcomes
            WHERE evaluated_at IS NOT NULL
              AND action IN ('BUY', 'SELL')
              AND return_pct IS NOT NULL
            ORDER BY signal_ts ASC
            """
        ).fetchall()
        x_rows: List[List[float]] = []
        y_rows: List[int] = []
        for (
            signal_ts,
            symbol,
            action,
            confidence,
            risk,
            reasons_json,
            council_enabled,
            council_changed,
            return_pct,
            directional_hit,
        ) in rows:
            reasons: List[str] = []
            if reasons_json:
                try:
                    parsed = json.loads(reasons_json)
                    if isinstance(parsed, list):
                        reasons = [str(x) for x in parsed]
                except Exception:
                    pass
            feat = build_feature_row(
                db_path=db_path,
                signal_ts=int(signal_ts),
                symbol=str(symbol),
                action=str(action),
                confidence=float(confidence),
                risk=str(risk),
                reasons=reasons,
                council_enabled=bool(council_enabled),
                council_changed=bool(council_changed),
                conn=conn,
            )
            label = 1 if int(directional_hit or 0) == 1 and float(return_pct) > 0 else 0
            x_rows.append(row_to_vector(feat))
            y_rows.append(label)
        meta = {
            "samples": len(x_rows),
            "positive_rate": (sum(y_rows) / len(y_rows)) if y_rows else 0.0,
            "min_samples": min_samples,
        }
        return x_rows, y_rows, meta
    finally:
        conn.close()
