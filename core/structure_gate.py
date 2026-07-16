"""SMC checklist gate with retest — integrates with aggregator."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.candle_resample import load_candles_1m_sync, resample_ohlc
from core.smc_analysis import is_mid_range, range_position, classify_htf_trend, find_swings
from core.smc_retest import (
    RetestConfig,
    StructureSetupStore,
    levels_from_setup,
    setup_allows_action,
)
from core.setup_scoring import SetupScore, compute_setup_score


@dataclass
class StructureGateResult:
    allowed: bool
    reason: str = ""
    sl: Optional[float] = None
    tp: Optional[float] = None
    rr_ratio: float = 0.0
    setup_state: str = ""
    checklist: Dict[str, Any] = field(default_factory=dict)
    one_liner: str = ""


class StructureGate:
    def __init__(
        self,
        cfg: Optional[RetestConfig] = None,
        *,
        continuation_enabled: Optional[bool] = None,
    ) -> None:
        self.cfg = cfg or RetestConfig()
        self.store = StructureSetupStore(self.cfg)
        self._last_scan: Dict[str, int] = {}
        self._continuation_override = continuation_enabled

    def _config_from_agent(self) -> RetestConfig:
        try:
            from config import config

            a = config.agent
            cfg = RetestConfig(
                htf_minutes=int(getattr(a, "agg_structure_htf_minutes", 240)),
                ltf_minutes=int(getattr(a, "agg_structure_ltf_minutes", 15)),
                min_rr=float(getattr(a, "agg_structure_min_rr", 3.0)),
                range_edge_pct=float(getattr(a, "agg_structure_range_edge_pct", 0.30)),
                block_mid_range_pct=float(
                    getattr(a, "agg_structure_mid_range_block_pct", 0.40)
                ),
                retest_tolerance_pct=float(
                    getattr(a, "agg_structure_retest_tolerance_pct", 0.15)
                ),
                setup_ttl_sec=int(getattr(a, "agg_structure_setup_ttl_sec", 14400)),
                require_htf_trend=bool(getattr(a, "agg_structure_require_htf_trend", True)),
                block_range_trend=bool(getattr(a, "agg_structure_block_range", True)),
                continuation_enabled=bool(
                    getattr(a, "agg_structure_continuation_enabled", True)
                ),
                continuation_min_hold_bars=int(
                    getattr(a, "agg_structure_continuation_min_hold_bars", 4)
                ),
                continuation_min_wait_bars=int(
                    getattr(a, "agg_structure_continuation_min_wait_bars", 6)
                ),
                continuation_min_displacement_pct=float(
                    getattr(a, "agg_structure_continuation_min_displacement_pct", 0.25)
                ),
            )
        except Exception:
            cfg = self.cfg
        if self._continuation_override is not None:
            cfg.continuation_enabled = bool(self._continuation_override)
        return cfg

    def scan_symbol(
        self, db_path: str, symbol: str, *, as_of_ts: Optional[int] = None
    ) -> Optional[Any]:
        cfg = self._config_from_agent()
        self.store.cfg = cfg
        c1m = load_candles_1m_sync(
            db_path, symbol, limit=8000, end_ts=as_of_ts
        )
        if len(c1m) < 200:
            return None
        ltf = resample_ohlc(c1m, cfg.ltf_minutes * 60)
        htf = resample_ohlc(c1m, cfg.htf_minutes * 60)
        setup = self.store.update(symbol, ltf, htf, m1_candles=c1m)
        scan_ts = int(as_of_ts if as_of_ts is not None else time.time())
        self._last_scan[symbol.upper()] = scan_ts
        return setup

    def evaluate(
        self,
        *,
        db_path: str,
        symbol: str,
        action: str,
        entry_price: Optional[float],
        enabled: bool = True,
    ) -> StructureGateResult:
        return self.evaluate_at(
            db_path=db_path,
            symbol=symbol,
            action=action,
            entry_price=entry_price,
            as_of_ts=int(time.time()),
            enabled=enabled,
        )

    def evaluate_at(
        self,
        *,
        db_path: str,
        symbol: str,
        action: str,
        entry_price: Optional[float],
        as_of_ts: int,
        enabled: bool = True,
    ) -> StructureGateResult:
        act = (action or "").upper()
        if not enabled or act not in ("BUY", "SELL"):
            return StructureGateResult(True)

        cfg = self._config_from_agent()
        self.store.cfg = cfg

        sym = symbol.upper()
        last = self._last_scan.get(sym, 0)
        if sym not in self._last_scan or int(as_of_ts) - last > 60:
            self.scan_symbol(db_path, symbol, as_of_ts=as_of_ts)

        setup = self.store.get(symbol)
        c1m = load_candles_1m_sync(
            db_path, symbol, limit=2000, end_ts=as_of_ts
        )
        ltf = resample_ohlc(c1m, cfg.ltf_minutes * 60)
        htf = resample_ohlc(c1m, cfg.htf_minutes * 60)

        if ltf:
            setup = self.store.update(symbol, ltf, htf, m1_candles=c1m) or setup

        return self._decide(
            cfg=cfg,
            setup=setup,
            ltf=ltf,
            htf=htf,
            action=act,
            entry_price=entry_price,
        )

    def score_at(
        self,
        *,
        db_path: str,
        symbol: str,
        action: str,
        entry_price: Optional[float],
        as_of_ts: Optional[int] = None,
        aggregator_confidence: float = 0.0,
        ml_win_prob: float = 0.0,
        ev_passed: bool = True,
    ) -> SetupScore:
        ts = int(as_of_ts if as_of_ts is not None else time.time())
        act = (action or "").upper()
        if act not in ("BUY", "SELL"):
            return SetupScore(reason="Not a directional setup")

        cfg = self._config_from_agent()
        self.store.cfg = cfg
        sym = symbol.upper()
        last = self._last_scan.get(sym, 0)
        if sym not in self._last_scan or ts - last > 60:
            self.scan_symbol(db_path, symbol, as_of_ts=ts)

        setup = self.store.get(symbol)
        c1m = load_candles_1m_sync(db_path, symbol, limit=2000, end_ts=ts)
        ltf = resample_ohlc(c1m, cfg.ltf_minutes * 60)
        htf = resample_ohlc(c1m, cfg.htf_minutes * 60)
        if ltf:
            setup = self.store.update(symbol, ltf, htf, m1_candles=c1m) or setup

        pos = range_position(ltf) if ltf else 0.5
        trend = classify_htf_trend(find_swings(htf)) if htf else "range"
        entry = float(entry_price or (ltf[-1]["close"] if ltf else 0.0))

        return compute_setup_score(
            action=act,
            trend=trend,
            range_pos=pos,
            setup=setup,
            cfg=cfg,
            entry_price=entry,
            aggregator_confidence=aggregator_confidence,
            ml_win_prob=ml_win_prob,
            ev_passed=ev_passed,
        )

    def score_symbol(
        self,
        db_path: str,
        symbol: str,
        *,
        as_of_ts: Optional[int] = None,
        book_metrics: Optional[Any] = None,
    ) -> Optional[SetupScore]:
        """Best score for long or short on symbol (for forming watchlist)."""
        setup = self.store.get(symbol)
        if not setup:
            return None
        side = (setup.side or "").lower()
        action = "BUY" if side == "long" else "SELL"
        c1m = load_candles_1m_sync(
            db_path, symbol, limit=2000, end_ts=as_of_ts
        )
        entry = float(c1m[-1]["close"]) if c1m else None
        sc = self.score_at(
            db_path=db_path,
            symbol=symbol,
            action=action,
            entry_price=entry,
            as_of_ts=as_of_ts,
        )
        if sc and sc.phase == "ready" and entry and entry > 0:
            from core.market_context import enrich_score_with_market_context

            sc = enrich_score_with_market_context(
                sc,
                setup=self.store.get(symbol),
                entry_price=entry,
                book_metrics=book_metrics,
                db_path=db_path,
                symbol=symbol,
            )
        return sc

    def _decide(
        self,
        *,
        cfg: RetestConfig,
        setup: Any,
        ltf: List[Dict[str, Any]],
        htf: List[Dict[str, Any]],
        action: str,
        entry_price: Optional[float],
    ) -> StructureGateResult:
        act = action.upper()
        pos = range_position(ltf) if ltf else 0.5
        trend = classify_htf_trend(find_swings(htf)) if htf else "range"

        checklist: Dict[str, Any] = {
            "htf_trend": trend,
            "range_position": round(pos, 3),
            "not_mid_range": not is_mid_range(pos, block_mid_pct=cfg.block_mid_range_pct),
            "setup_state": setup.state if setup else "none",
            "liquidity_sweep": bool(setup and setup.checklist.get("liquidity_sweep")),
            "bos": bool(setup and setup.checklist.get("bos")),
            "retest": bool(setup and setup.checklist.get("retest")),
        }

        if cfg.block_range_trend and trend == "range":
            return StructureGateResult(
                False,
                "Structure: HTF range — no trade.",
                checklist=checklist,
                setup_state=setup.state if setup else "none",
            )

        if is_mid_range(pos, block_mid_pct=cfg.block_mid_range_pct):
            return StructureGateResult(
                False,
                "Structure: price in mid-range — skip.",
                checklist=checklist,
                setup_state=setup.state if setup else "none",
            )

        if act == "BUY" and trend == "down":
            return StructureGateResult(
                False,
                "Structure: HTF downtrend — no Long.",
                checklist=checklist,
            )
        if act == "SELL" and trend == "up":
            return StructureGateResult(
                False,
                "Structure: HTF uptrend — no Short.",
                checklist=checklist,
            )

        if not setup:
            return StructureGateResult(
                False,
                "Structure: no setup (need sweep → BOS → retest).",
                checklist=checklist,
                setup_state="none",
            )

        if setup.state == "await_bos":
            return StructureGateResult(
                False,
                "Structure: liquidity swept — waiting for BOS.",
                checklist=checklist,
                setup_state=setup.state,
            )

        if setup.state == "await_retest":
            z = setup.zone
            return StructureGateResult(
                False,
                f"Structure: BOS done — awaiting retest of {z.kind} [{z.low:.4f}-{z.high:.4f}].",
                checklist=checklist,
                setup_state=setup.state,
            )

        if not setup_allows_action(setup, act):
            return StructureGateResult(
                False,
                f"Structure: ready setup is {setup.side}, not {act}.",
                checklist=checklist,
                setup_state=setup.state,
            )

        entry = float(entry_price or ltf[-1]["close"] if ltf else 0.0)
        if entry <= 0:
            return StructureGateResult(False, "Structure: missing entry price.", checklist=checklist)

        sl, tp, rr = levels_from_setup(setup, entry, cfg.min_rr)
        if rr < cfg.min_rr:
            return StructureGateResult(
                False,
                f"Structure: RR {rr:.1f} < min {cfg.min_rr:.1f}.",
                checklist=checklist,
                setup_state=setup.state,
            )

        one_liner = (
            f"{trend.upper()} {setup.side}: sweep→BOS→retest {setup.zone.kind}, "
            f"SL struct, TP liq, RR 1:{rr:.1f}"
        )
        checklist["rr_ok"] = True
        checklist["retest"] = True

        return StructureGateResult(
            True,
            f"Structure OK: {one_liner}",
            sl=sl,
            tp=tp,
            rr_ratio=rr,
            setup_state="ready",
            checklist=checklist,
            one_liner=one_liner,
        )
