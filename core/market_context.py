"""Market context: order-book liquidity, open interest, move potential."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.setup_scoring import SetupScore


@dataclass
class BookMetrics:
    imbalance: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    spread_pct: float = 0.0
    thin_book: bool = False
    book_aligned: bool = False


@dataclass
class OiMetrics:
    available: bool = False
    open_interest: float = 0.0
    change_pct: float = 0.0
    oi_aligned: bool = False
    oi_divergence: bool = False


@dataclass
class PotentialMetrics:
    displacement_pct: float = 0.0
    tp_room_pct: float = 0.0
    sl_distance_pct: float = 0.0
    potential_ok: bool = True


@dataclass
class MarketContext:
    book: BookMetrics = field(default_factory=BookMetrics)
    oi: OiMetrics = field(default_factory=OiMetrics)
    potential: PotentialMetrics = field(default_factory=PotentialMetrics)
    gate_ok: bool = True
    gate_reason: str = ""
    score_delta: int = 0


def analyze_orderbook(
    bids: List,
    asks: List,
    action: str,
    *,
    min_depth_usd: float = 50_000.0,
    max_spread_pct: float = 0.15,
    min_imbalance: float = 0.08,
) -> BookMetrics:
    if not bids or not asks:
        return BookMetrics(thin_book=True)

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2.0 if (best_bid + best_ask) > 0 else 0.0
    spread_pct = ((best_ask - best_bid) / mid * 100.0) if mid > 0 else 99.0

    bid_depth = sum(float(p) * float(a) for p, a in bids[:10])
    ask_depth = sum(float(p) * float(a) for p, a in asks[:10])
    total = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0

    thin = (
        total < min_depth_usd
        or spread_pct > max_spread_pct
    )
    act = (action or "").upper()
    aligned = False
    if act == "BUY":
        aligned = imbalance >= min_imbalance
    elif act == "SELL":
        aligned = imbalance <= -min_imbalance

    return BookMetrics(
        imbalance=imbalance,
        bid_depth_usd=bid_depth,
        ask_depth_usd=ask_depth,
        spread_pct=spread_pct,
        thin_book=thin,
        book_aligned=aligned,
    )


def load_oi_metrics_sync(
    db_path: str,
    symbol: str,
    action: str,
    *,
    lookback_sec: int = 14_400,
    min_aligned_change_pct: float = 0.5,
    divergence_block_pct: float = 2.0,
) -> OiMetrics:
    sym = symbol.upper()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT timestamp, open_interest FROM open_interest_snapshots
            WHERE symbol=? ORDER BY timestamp DESC LIMIT 200
            """,
            (sym,),
        ).fetchall()
    except sqlite3.OperationalError:
        return OiMetrics()
    finally:
        conn.close()

    if not rows:
        return OiMetrics()

    current_oi = float(rows[0][1])
    current_ts = int(rows[0][0])
    past_oi = current_oi
    for ts, oi in rows:
        if current_ts - int(ts) >= lookback_sec:
            past_oi = float(oi)
            break

    change_pct = 0.0
    if past_oi > 0:
        change_pct = (current_oi - past_oi) / past_oi * 100.0

    act = (action or "").upper()
    aligned = False
    divergence = False
    if act == "BUY":
        aligned = change_pct >= min_aligned_change_pct
        divergence = change_pct <= -divergence_block_pct
    elif act == "SELL":
        aligned = change_pct <= -min_aligned_change_pct
        divergence = change_pct >= divergence_block_pct

    return OiMetrics(
        available=True,
        open_interest=current_oi,
        change_pct=change_pct,
        oi_aligned=aligned,
        oi_divergence=divergence,
    )


def compute_potential_metrics(
    setup: Any,
    entry: float,
    sl: float,
    tp: float,
    action: str,
    *,
    min_tp_room_pct: float = 1.0,
    min_displacement_pct: float = 0.25,
    min_sl_pct: float = 0.45,
    entry_mode: str = "retest",
) -> PotentialMetrics:
    act = (action or "").upper()
    sl_dist = tp_room = disp = 0.0
    if entry > 0 and sl > 0 and tp > 0:
        if act == "BUY":
            sl_dist = (entry - sl) / entry * 100.0
            tp_room = (tp - entry) / entry * 100.0
        elif act == "SELL":
            sl_dist = (sl - entry) / entry * 100.0
            tp_room = (entry - tp) / entry * 100.0

    # Impulse = BOS distance from zone/sweep (not entry vs BOS).
    # On retest entry is back near the zone, so entry-vs-BOS is small by design.
    if setup and getattr(setup, "bos_price", 0):
        bos = float(setup.bos_price)
        zone = getattr(setup, "zone", None)
        ref = 0.0
        if zone is not None:
            ref = (float(zone.low) + float(zone.high)) / 2.0
        elif getattr(setup, "sweep_price", 0):
            ref = float(setup.sweep_price)
        side = (getattr(setup, "side", "") or "").lower()
        if bos > 0 and ref > 0:
            if side == "long":
                disp = (bos - ref) / ref * 100.0
            elif side == "short":
                disp = (ref - bos) / ref * 100.0

    ok = True
    if sl_dist > 0 and sl_dist < min_sl_pct:
        ok = False
    if tp_room < min_tp_room_pct:
        ok = False
    if disp < min_displacement_pct:
        ok = False

    return PotentialMetrics(
        displacement_pct=disp,
        tp_room_pct=tp_room,
        sl_distance_pct=sl_dist,
        potential_ok=ok,
    )


def _load_gate_config() -> Dict[str, Any]:
    try:
        from config import config

        a = config.agent
        return {
            "enabled": bool(getattr(a, "market_context_gate_enabled", True)),
            "min_depth_usd": float(getattr(a, "market_min_book_depth_usd", 50_000.0)),
            "max_spread_pct": float(getattr(a, "market_max_spread_pct", 0.15)),
            "min_imbalance": float(getattr(a, "market_min_book_imbalance", 0.08)),
            "min_tp_room_pct": float(getattr(a, "market_min_tp_room_pct", 1.0)),
            "min_displacement_pct": float(
                getattr(a, "market_min_displacement_pct", 0.25)
            ),
            "min_sl_pct": float(getattr(a, "agg_structure_min_sl_pct", 0.45)),
            "oi_lookback_sec": int(
                float(getattr(a, "oi_change_lookback_hours", 4.0)) * 3600
            ),
            "oi_min_aligned_pct": float(getattr(a, "oi_min_change_aligned_pct", 0.5)),
            "oi_divergence_block_pct": float(
                getattr(a, "oi_divergence_block_pct", 2.0)
            ),
            "require_book_aligned": bool(
                getattr(a, "market_require_book_aligned", False)
            ),
            "require_oi_aligned": bool(getattr(a, "oi_require_aligned", False)),
        }
    except Exception:
        return {"enabled": True}


def build_market_context(
    *,
    action: str,
    setup: Any,
    entry: float,
    sl: float,
    tp: float,
    entry_mode: str = "retest",
    book_metrics: Optional[BookMetrics] = None,
    oi_metrics: Optional[OiMetrics] = None,
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> MarketContext:
    cfg = _load_gate_config()
    book = book_metrics or BookMetrics()
    oi = oi_metrics
    if oi is None and db_path and symbol:
        oi = load_oi_metrics_sync(
            db_path,
            symbol,
            action,
            lookback_sec=int(cfg.get("oi_lookback_sec", 14_400)),
            min_aligned_change_pct=float(cfg.get("oi_min_aligned_pct", 0.5)),
            divergence_block_pct=float(cfg.get("oi_divergence_block_pct", 2.0)),
        )
    if oi is None:
        oi = OiMetrics()

    potential = compute_potential_metrics(
        setup,
        entry,
        sl,
        tp,
        action,
        min_tp_room_pct=float(cfg.get("min_tp_room_pct", 1.0)),
        min_displacement_pct=float(cfg.get("min_displacement_pct", 0.25)),
        min_sl_pct=float(cfg.get("min_sl_pct", 0.45)),
        entry_mode=entry_mode,
    )

    gate_ok = True
    reasons: List[str] = []
    if cfg.get("enabled", True):
        if book.thin_book:
            gate_ok = False
            reasons.append("тонкий стакан")
        if not potential.potential_ok:
            gate_ok = False
            if potential.sl_distance_pct < float(cfg.get("min_sl_pct", 0.45)):
                reasons.append(f"SL {potential.sl_distance_pct:.2f}%")
            if potential.tp_room_pct < float(cfg.get("min_tp_room_pct", 1.0)):
                reasons.append(f"мало потенциала {potential.tp_room_pct:.2f}%")
            if potential.displacement_pct < float(cfg.get("min_displacement_pct", 0.25)):
                reasons.append(f"слабый импульс {potential.displacement_pct:.2f}%")
        if oi.available and oi.oi_divergence:
            gate_ok = False
            reasons.append(f"OI против ({oi.change_pct:+.1f}%)")
        if cfg.get("require_book_aligned") and not book.book_aligned:
            gate_ok = False
            reasons.append("стакан не подтверждает")
        if cfg.get("require_oi_aligned") and oi.available and not oi.oi_aligned:
            gate_ok = False
            reasons.append("OI не подтверждает")

    score_delta = 0
    if book.book_aligned and not book.thin_book:
        score_delta += 8
    if book.thin_book:
        score_delta -= 20
    if oi.available:
        if oi.oi_aligned:
            score_delta += 12
        if oi.oi_divergence:
            score_delta -= 15
    if potential.potential_ok:
        if potential.tp_room_pct >= float(cfg.get("min_tp_room_pct", 1.0)) * 1.5:
            score_delta += 10
    else:
        score_delta -= 12
    if potential.displacement_pct >= float(cfg.get("min_displacement_pct", 0.25)) * 2:
        score_delta += 6

    return MarketContext(
        book=book,
        oi=oi,
        potential=potential,
        gate_ok=gate_ok,
        gate_reason="; ".join(reasons),
        score_delta=score_delta,
    )


def enrich_score_with_market_context(
    score: SetupScore,
    *,
    setup: Any,
    entry_price: float,
    book_metrics: Optional[BookMetrics] = None,
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> SetupScore:
    if score.phase != "ready" or not score.sl or not score.tp:
        return score

    entry = float(entry_price or 0)
    if entry <= 0:
        return score

    ctx = build_market_context(
        action=score.aligned_action,
        setup=setup,
        entry=entry,
        sl=float(score.sl),
        tp=float(score.tp),
        entry_mode=score.entry_mode,
        book_metrics=book_metrics,
        db_path=db_path,
        symbol=symbol,
    )

    score.quality_score = max(0, min(100, score.quality_score + ctx.score_delta))
    score.components["market_context"] = ctx.score_delta
    if ctx.book.book_aligned:
        score.components["book_support"] = 8
    if ctx.book.thin_book:
        score.components["thin_book"] = -20
    if ctx.oi.oi_aligned:
        score.components["oi_aligned"] = 12
    if ctx.oi.oi_divergence:
        score.components["oi_divergence"] = -15
    if ctx.potential.potential_ok:
        score.components["tp_room"] = 10
    else:
        score.components["no_room"] = -12

    score.checklist.update(
        {
            "market_gate_ok": ctx.gate_ok,
            "book_imbalance": round(ctx.book.imbalance, 3),
            "book_aligned": ctx.book.book_aligned,
            "thin_book": ctx.book.thin_book,
            "spread_pct": round(ctx.book.spread_pct, 4),
            "book_depth_usd": round(
                ctx.book.bid_depth_usd + ctx.book.ask_depth_usd, 0
            ),
            "oi_available": ctx.oi.available,
            "oi_change_pct": round(ctx.oi.change_pct, 2),
            "oi_aligned": ctx.oi.oi_aligned,
            "oi_divergence": ctx.oi.oi_divergence,
            "displacement_pct": round(ctx.potential.displacement_pct, 3),
            "tp_room_pct": round(ctx.potential.tp_room_pct, 3),
            "sl_distance_pct": round(ctx.potential.sl_distance_pct, 3),
        }
    )

    if not ctx.gate_ok:
        score.checklist["market_gate_ok"] = False
        score.checklist["market_gate_reason"] = ctx.gate_reason
        score.components["market_gate"] = -8
        score.quality_score = max(0, score.quality_score - 8)
        suffix = f" [{ctx.gate_reason}]" if ctx.gate_reason else ""
        score.reason = (score.reason or "") + f" (market soft{suffix})"
    else:
        score.checklist["market_gate_ok"] = True

    return score
