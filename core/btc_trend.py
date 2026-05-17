"""BTC short-horizon trend for altcoin BUY gating."""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

BTC_SYMBOLS = frozenset({"BTCUSDT", "BTC"})


def classify_trend(
    return_pct: float,
    *,
    down_threshold_pct: float = -0.08,
    up_threshold_pct: float = 0.08,
) -> str:
    if return_pct <= down_threshold_pct:
        return "down"
    if return_pct >= up_threshold_pct:
        return "up"
    return "flat"


def is_alt_symbol(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    return bool(sym) and sym not in BTC_SYMBOLS


def blocks_alt_buy(
    symbol: str,
    action: str,
    trend: str,
    *,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False
    if (action or "").upper() != "BUY":
        return False
    if not is_alt_symbol(symbol):
        return False
    return trend == "down"


def btc_trend_block_reason(return_pct: float) -> str:
    return f"BTC trend filter: 30m BTC return {return_pct:.3f}% (down)."


def _fetch_closes_sync(
    db_path: str,
    *,
    symbol: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
) -> List[float]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        for sym in (symbol, symbol.upper(), symbol.lower()):
            cur.execute(
                """
                SELECT close FROM candles
                WHERE symbol = ? AND timeframe = ?
                  AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (sym, timeframe, start_ts, end_ts),
            )
            rows = cur.fetchall()
            if rows:
                return [float(r[0]) for r in rows if r[0] is not None]
        return []
    finally:
        conn.close()


def btc_trend_at_ts(
    db_path: str,
    end_ts: int,
    *,
    lookback_minutes: int = 30,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    down_threshold_pct: float = -0.08,
    up_threshold_pct: float = 0.08,
) -> Dict[str, object]:
    start_ts = int(end_ts) - int(lookback_minutes) * 60
    closes = _fetch_closes_sync(
        db_path,
        symbol=symbol,
        timeframe=timeframe,
        start_ts=start_ts,
        end_ts=int(end_ts),
    )
    if len(closes) < 2:
        return {
            "trend": "unknown",
            "return_pct": None,
            "candles": len(closes),
            "start_ts": start_ts,
            "end_ts": int(end_ts),
        }
    first, last = closes[0], closes[-1]
    if first <= 0:
        return {"trend": "unknown", "return_pct": None, "candles": len(closes)}
    ret_pct = (last - first) / first * 100.0
    trend = classify_trend(
        ret_pct,
        down_threshold_pct=down_threshold_pct,
        up_threshold_pct=up_threshold_pct,
    )
    return {
        "trend": trend,
        "return_pct": ret_pct,
        "candles": len(closes),
        "start_ts": start_ts,
        "end_ts": int(end_ts),
    }


class BtcTrendCache:
  """Minute-bucket cache for replay loops."""

  def __init__(self) -> None:
      self._cache: Dict[int, Dict[str, object]] = {}

  def get(
      self,
      db_path: str,
      ts: int,
      **kwargs: object,
  ) -> Dict[str, object]:
      key = int(ts) // 60
      if key not in self._cache:
          self._cache[key] = btc_trend_at_ts(db_path, ts, **kwargs)  # type: ignore[arg-type]
      return self._cache[key]
