"""Shared entry-quality rules for live aggregator and historical replay."""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set, Tuple

from core.bearish_regime import bearish_regime_reason, is_bearish_regime
from core.btc_trend import blocks_alt_buy, blocks_alt_sell, btc_trend_block_reason

QUALITY_AGENTS = frozenset({"market", "onchain", "liquidity"})
SELL_QUALITY_AGENTS = frozenset({"market", "emergency", "liquidity"})


def _agent_type(signal: Any) -> str:
    return str(getattr(signal, "agent_type", "") or "").lower()


def unique_agent_types(signals: Iterable[Any]) -> Set[str]:
    return {_agent_type(s) for s in signals if _agent_type(s)}


def passes_entry_quality(
    action: str,
    confidence: float,
    buy_signals: List[Any],
    sell_signals: List[Any],
    *,
    min_unique_agents: int,
    min_directional_confidence: float,
    require_quality_agent_for_buy: bool = True,
    require_quality_agent_for_sell: bool = True,
    exit_signals: Optional[List[Any]] = None,
    emergency_count: int = 0,
    bearish_pressure: int = 0,
    buy_score: float = 0.0,
    sell_score: float = 0.0,
    bearish_regime_enabled: bool = True,
    symbol: str = "",
    btc_trend: Optional[str] = None,
    btc_trend_return_pct: Optional[float] = None,
    btc_trend_filter_enabled: bool = True,
) -> Tuple[bool, str]:
    """
    Extra filter after scoring/EV/strategy.
    Reduces low-confluence and shitcoin-only directional entries.
    """
    act = (action or "").upper()
    if act not in ("BUY", "SELL"):
        return True, ""

    eff_min_agents = int(min_unique_agents)
    eff_min_conf = float(min_directional_confidence)
    if act == "SELL" and is_bearish_regime(
        buy_count=len(buy_signals),
        sell_count=len(sell_signals),
        exit_count=len(exit_signals or []),
        emergency_count=int(emergency_count),
        bearish_pressure=int(bearish_pressure),
        buy_score=float(buy_score),
        sell_score=float(sell_score),
    ):
        eff_min_agents = max(1, eff_min_agents - 1)
        eff_min_conf = max(0.50, eff_min_conf - 0.05)

    side_signals = buy_signals if act == "BUY" else sell_signals
    agents = unique_agent_types(side_signals)
    if len(agents) < eff_min_agents:
        return False, f"Entry quality: need {eff_min_agents}+ agents, got {len(agents)}."

    if confidence < eff_min_conf:
        return (
            False,
            f"Entry quality: confidence {confidence:.2f} < {eff_min_conf:.2f}.",
        )

    if act == "BUY" and require_quality_agent_for_buy:
        if not (agents & QUALITY_AGENTS):
            return False, "Entry quality: BUY requires market/onchain/liquidity confirmation."

    if act == "SELL" and require_quality_agent_for_sell:
        if not (agents & SELL_QUALITY_AGENTS):
            return False, "Entry quality: SELL requires market/emergency/liquidity confirmation."

    if act == "BUY" and bearish_regime_enabled:
        exits = exit_signals or []
        if is_bearish_regime(
            buy_count=len(buy_signals),
            sell_count=len(sell_signals),
            exit_count=len(exits),
            emergency_count=int(emergency_count),
            bearish_pressure=int(bearish_pressure),
            buy_score=float(buy_score),
            sell_score=float(sell_score),
        ):
            return False, bearish_regime_reason()

    if (
        act == "BUY"
        and btc_trend_filter_enabled
        and blocks_alt_buy(symbol, act, str(btc_trend or "unknown"), enabled=True)
    ):
        ret = btc_trend_return_pct if btc_trend_return_pct is not None else 0.0
        return False, btc_trend_block_reason(float(ret))

    if (
        act == "SELL"
        and btc_trend_filter_enabled
        and blocks_alt_sell(symbol, act, str(btc_trend or "unknown"), enabled=True)
    ):
        ret = btc_trend_return_pct if btc_trend_return_pct is not None else 0.0
        return False, f"BTC trend filter: 30m BTC return {float(ret):.3f}% (up), alt SELL blocked."

    return True, ""
