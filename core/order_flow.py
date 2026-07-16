"""Taker buy/sell flow from recent agg trades (Binance is_buyer_maker)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TradeFlowMetrics:
    buy_usd: float = 0.0
    sell_usd: float = 0.0
    delta_usd: float = 0.0
    delta_pct: float = 0.0
    buy_ratio: float = 0.5
    trade_count: int = 0
    flow_aligned: bool = False
    dominance: str = "neutral"  # buyers | sellers | neutral


def analyze_trade_flow(
    trades: List[Dict[str, Any]],
    action: str,
    *,
    min_trades: int = 15,
    min_delta_pct: float = 0.08,
) -> TradeFlowMetrics:
    """
    Binance aggTrade: is_buyer_maker=True → seller aggressor (taker sell).
    is_buyer_maker=False → buyer aggressor (taker buy).
    """
    if not trades:
        return TradeFlowMetrics()

    buy_usd = sell_usd = 0.0
    for t in trades:
        px = float(t.get("price") or 0)
        qty = float(t.get("quantity") or 0)
        if px <= 0 or qty <= 0:
            continue
        usd = px * qty
        if t.get("is_buyer_maker"):
            sell_usd += usd
        else:
            buy_usd += usd

    total = buy_usd + sell_usd
    if total <= 0 or len(trades) < min_trades:
        return TradeFlowMetrics(trade_count=len(trades))

    delta = buy_usd - sell_usd
    delta_pct = delta / total * 100.0
    buy_ratio = buy_usd / total
    act = (action or "").upper()

    dominance = "neutral"
    if delta_pct >= min_delta_pct:
        dominance = "buyers"
    elif delta_pct <= -min_delta_pct:
        dominance = "sellers"

    aligned = False
    if act == "BUY":
        aligned = delta_pct >= min_delta_pct
    elif act == "SELL":
        aligned = delta_pct <= -min_delta_pct

    return TradeFlowMetrics(
        buy_usd=buy_usd,
        sell_usd=sell_usd,
        delta_usd=delta,
        delta_pct=delta_pct,
        buy_ratio=buy_ratio,
        trade_count=len(trades),
        flow_aligned=aligned,
        dominance=dominance,
    )
