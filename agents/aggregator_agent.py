"""Aggregator agent: merges signals from all agents into weighted actions."""
import asyncio
from calendar import timegm
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from core.utils import is_stable_coin, validate_price
from core.metrics import Metrics
from core.agent_weights import load_agent_weights
from core.bearish_regime import bearish_regime_reason, is_bearish_regime
from core.btc_trend import btc_trend_at_ts
from core.entry_quality import passes_entry_quality
from core.edge_calibration import calibration_extra_bps, load_calibration
from core.ev_edge import evaluate_edge_gate
from core.expert_council import refine_aggregate
from core.signal_model import SignalModelGate
from core.trade_levels import breakeven_win_rate, compute_trade_levels, passes_min_rr
from core.structure_gate import StructureGate
from core.market_context import build_market_context, BookMetrics
from config import config


class Action(Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    WAIT = "WAIT"


class RiskLevel(Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class AggregatedSignal:
    def __init__(self, symbol: str, action: Action, risk: RiskLevel, 
                 confidence: float, reasons: List[str], price: Optional[float] = None,
                 entry: Optional[float] = None, sl: Optional[float] = None, 
                 tp: Optional[float] = None):
        self.symbol = symbol
        self.action = action
        self.risk = risk
        self.confidence = confidence  # 0.0 - 1.0
        self.reasons = reasons
        self.price = price
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.timestamp = datetime.utcnow()
        self.source_signals = []  # contributing raw signals
        self.baseline_action: Optional[str] = None  # set before expert council
        self.setup_quality: int = 0
        self.setup_phase: str = "none"
        self.win_probability: float = 0.0
        self.entry_mode: str = "none"

    def __repr__(self):
        return f"AggregatedSignal({self.symbol}, {self.action.value}, {self.confidence:.2%})"


class AggregatorAgent:
    def __init__(
        self,
        db: Database,
        event_router: EventRouter,
        telegram_bot=None,
        *,
        liquidity_agent=None,
        market_agent=None,
    ):
        self.db = db
        self.event_router = event_router
        self.telegram_bot = telegram_bot
        self.liquidity_agent = liquidity_agent
        self.market_agent = market_agent
        self.running = False
        self.logger = get_logger(__name__)
        self.metrics = Metrics(db)
        self.logger.info(
            "Aggregator strategy mode=%s (min_conf=%.2f confirms=%s)",
            getattr(config.agent, "strategy_mode", "balanced"),
            float(getattr(config.agent, "strategy_min_confidence", 0.55)),
            int(getattr(config.agent, "strategy_required_confirmations", 2)),
        )
        
        self.signals_by_symbol = defaultdict(list)
        self.last_sent_signals = {}
        self.stable_coins = config.stable_coins
        self.signal_weights = {
            "emergency": {
                "price_spike": 1.0,
                "volume_spike": 0.8,
                "dump_danger": 1.0,
                "liquidity_crisis": 0.9
            },
            "market": {
                "resistance_break": 0.7,
                "support_break": 0.7,
                "volume_spike": 0.6,
                "high_volatility": 0.4
            },
            "onchain": {
                "whale_activity": 0.8,
                "whale_alert": 0.9
            },
            "liquidity": {
                "orderbook_imbalance": 0.6,
                "stop_cluster": 0.7,
                "liquidity_break": 0.8
            },
            "shitcoin": {
                "pump": 0.45,
                "dump": 0.55,
                "rapid_pump": 0.5,
                "rapid_dump": 0.55,
                "new_shitcoin": 0.2,
            },
        }
        
        self.priority_weights = {
            Priority.CRITICAL: 1.0,
            Priority.URGENT: 0.9,
            Priority.HIGH: 0.7,
            Priority.MEDIUM: 0.4,
            Priority.LOW: 0.2
        }
        self.agent_weight_mult = load_agent_weights()
        if self.agent_weight_mult:
            self.logger.info("Loaded agent weight multipliers: %s", self.agent_weight_mult)
        self.edge_calibration = load_calibration()
        if self.edge_calibration.get("buckets"):
            self.logger.info(
                "Loaded edge calibration buckets: %s",
                len(self.edge_calibration.get("buckets", {})),
            )
        self.signal_model_gate = SignalModelGate()
        if self.signal_model_gate.ready:
            self.logger.info(
                "Loaded ML signal model (test_acc=%.3f)",
                float(self.signal_model_gate.meta.get("test_accuracy") or 0.0),
            )
        self.structure_gate = StructureGate()
        self._forming_alerts_today = 0
        self._ready_alerts_today = 0
        self._alert_day_key = ""
        self._last_forming_alert: Dict[str, int] = {}
        self._last_ready_alert: Dict[str, int] = {}
        self._last_ready_fingerprint: Dict[str, str] = {}
        self._last_ready_fingerprint_ts: Dict[str, int] = {}
        self._last_volume_spike_alert: Dict[str, int] = {}
        self._last_volume_spike_ratio: Dict[str, float] = {}
        self._last_exit_alert: Dict[str, int] = {}
        import time as _time

        grace = int(getattr(config.agent, "agg_structure_startup_grace_sec", 180) or 0)
        self._ready_grace_until = _time.time() + max(0, grace)
        if getattr(config.agent, "analyst_mode_enabled", True):
            forming_on = bool(getattr(config.agent, "analyst_forming_alerts_enabled", False))
            self.logger.info(
                "Analyst mode ON (ready>=%s, forming=%s/%s, min_win=%.0f%%, "
                "max_ready/day=%s, structure_only_tg=%s)",
                int(getattr(config.agent, "analyst_ready_min_score", 78)),
                "on" if forming_on else "off",
                int(getattr(config.agent, "analyst_forming_min_score", 70)),
                float(getattr(config.agent, "analyst_min_win_probability", 0.50)) * 100,
                "unlimited"
                if int(getattr(config.agent, "analyst_max_alerts_per_day", 0)) <= 0
                else int(getattr(config.agent, "analyst_max_alerts_per_day", 0)),
                bool(getattr(config.agent, "analyst_structure_only_telegram", True)),
            )
        if getattr(config.agent, "market_context_gate_enabled", True):
            self.logger.info(
                "Market context gate ON (OI=%s, min_tp=%.1f%%, min_disp=%.2f%%)",
                "on" if getattr(config.agent, "oi_enabled", True) else "off",
                float(getattr(config.agent, "market_min_tp_room_pct", 1.0)),
                float(getattr(config.agent, "market_min_displacement_pct", 0.25)),
            )
        self.logger.info("Structure gate (SMC + retest) enabled")

        self.signal_queue = asyncio.Queue()

    def _ready_trade_levels(
        self, symbol: str, entry: float, action: str
    ) -> Optional[Tuple[float, float, float, str]]:
        """Structural SL/TP with volatility floor and liquidity-based TP."""
        from core.candle_resample import load_candles_1m_sync, resample_ohlc
        from core.liquidity_targets import load_recent_book_zones_sync
        from core.smc_analysis import find_swings
        from core.structure_levels import finalize_structure_levels, htf_liquidity_target

        setup = self.structure_gate.store.get(symbol)
        if not setup or entry <= 0:
            return None
        cfg = self.structure_gate._config_from_agent()
        vol = self._symbol_vol_pct_sync(
            symbol, int(datetime.utcnow().timestamp())
        )
        min_sl = float(getattr(config.agent, "agg_structure_min_sl_pct", 0.55))
        max_tp = float(getattr(config.agent, "agg_tp_max_pct", 8.0))
        min_tp = float(getattr(config.agent, "market_min_tp_room_pct", 1.2))
        max_sl = float(getattr(config.agent, "agg_structure_max_sl_pct", 2.5))

        c1m = load_candles_1m_sync(self.db.db_path, symbol, limit=8000)
        ltf = resample_ohlc(c1m, cfg.ltf_minutes * 60)
        htf = resample_ohlc(c1m, cfg.htf_minutes * 60)
        ltf_swings = find_swings(ltf)
        htf_swings = find_swings(htf)
        book_zones = load_recent_book_zones_sync(self.db.db_path, symbol)
        stop_clusters: list = []
        if self.liquidity_agent:
            stop_clusters = self.liquidity_agent.get_stop_clusters(symbol)
            for z in self.liquidity_agent.get_liquidity_levels_for_tp(
                symbol, action, float(entry)
            ):
                if z not in book_zones:
                    book_zones.append(z)

        fl = finalize_structure_levels(
            setup,
            float(entry),
            action,
            min_rr=cfg.min_rr,
            min_sl_pct=min_sl,
            sl_pct=float(getattr(config.agent, "agg_sl_pct", 0.35)),
            tp_rr_ratio=float(getattr(config.agent, "agg_tp_rr_ratio", 3.0)),
            volatility_pct=vol if vol > 0 else None,
            ltf_swings=ltf_swings,
            htf_swings=htf_swings,
            stop_clusters=stop_clusters,
            book_zones=book_zones,
            htf_target=htf_liquidity_target(htf_swings, setup.side, float(entry)),
            min_tp_pct=min_tp,
            max_tp_pct=max_tp,
            max_sl_pct=max_sl,
        )
        if not fl:
            return None
        if fl.widened_sl:
            self.logger.debug(
                "Widened SL %s: %.2f%% (vol/min floor applied)",
                symbol,
                fl.sl_pct,
            )
        if fl.tp_source and fl.tp_source != "rr_floor":
            self.logger.debug(
                "TP %s @ %s (RR 1:%.1f, dist %.2f%%)",
                symbol,
                fl.tp_source,
                fl.rr_ratio,
                fl.tp_pct,
            )
        return fl.sl, fl.tp, fl.rr_ratio, fl.tp_source

    async def _send_structure_telegram_alert(
        self,
        *,
        symbol: str,
        sc,
        setup_phase: str,
        entry_px: Optional[float] = None,
        sl_px: Optional[float] = None,
        tp_px: Optional[float] = None,
        tp_source: str = "",
    ) -> bool:
        """Push SMC structure alert (forming or ready) with chart."""
        if not self.telegram_bot or not sc:
            return False
        from core.liquidity_targets import liquidity_kind_label
        from core.trade_chart import build_trade_chart_bytes

        setup = self.structure_gate.store.get(symbol)
        zone_low = zone_high = None
        if setup and getattr(setup, "zone", None):
            zone_low = float(setup.zone.low)
            zone_high = float(setup.zone.high)
        entry = entry_px
        if entry is None and setup_phase == "ready":
            entry = self._latest_close_sync(symbol)
        sl = sl_px if sl_px is not None else sc.sl
        tp = tp_px if tp_px is not None else sc.tp
        tp_label = liquidity_kind_label(tp_source) if tp_source else ""
        cfg = self.structure_gate._config_from_agent()
        loop = asyncio.get_event_loop()
        chart = await loop.run_in_executor(
            None,
            lambda: build_trade_chart_bytes(
                self.db.db_path,
                symbol,
                action=sc.aligned_action,
                entry=entry,
                sl=sl,
                tp=tp,
                zone_low=zone_low,
                zone_high=zone_high,
                bar_minutes=int(cfg.ltf_minutes),
                bars=96,
                tp_source=tp_label,
            ),
        )
        return await self.telegram_bot.send_trade_alert(
            symbol=symbol,
            action=sc.aligned_action,
            entry=entry,
            sl=sl,
            tp=tp,
            confidence=sc.quality_score / 100.0,
            risk="Medium",
            reasons=[sc.reason],
            chart_bytes=chart,
            setup_quality=sc.quality_score,
            setup_phase=setup_phase,
            win_probability=sc.win_probability,
            setup_checklist=sc.checklist,
            watch_zone_low=zone_low,
            watch_zone_high=zone_high,
            current_price=entry or self._latest_close_sync(symbol),
            entry_mode=getattr(sc, "entry_mode", "none"),
            tp_liquidity_source=tp_source,
        )

    async def _register_paper_trade(
        self,
        *,
        symbol: str,
        action: str,
        entry: Optional[float],
        sl: Optional[float],
        tp: Optional[float],
        setup_quality: int,
        entry_mode: str,
    ) -> None:
        if not entry or not sl or not tp or entry <= 0 or sl <= 0 or tp <= 0:
            return
        from core.partial_exit import resolve_tp1

        tp1 = None
        if getattr(config.agent, "paper_partial_enabled", True):
            tp1 = resolve_tp1(
                float(entry),
                float(sl),
                float(tp),
                action,
                partial_rr=float(getattr(config.agent, "paper_partial_rr", 1.5)),
                adaptive=bool(getattr(config.agent, "paper_partial_adaptive", True)),
                min_tp_pct=float(getattr(config.agent, "paper_partial_min_tp_pct", 3.5)),
                min_rr=float(getattr(config.agent, "paper_partial_min_rr", 2.8)),
            )
            if tp1 and tp1 > 0:
                self.logger.debug(
                    "Paper %s adaptive partial ON tp1=%.6g (tp far)",
                    symbol,
                    tp1,
                )
        await self.db.open_paper_trade(
            symbol=symbol,
            action=action,
            entry=float(entry),
            sl=float(sl),
            tp=float(tp),
            setup_quality=int(setup_quality or 0),
            entry_mode=entry_mode or "",
            tp1=tp1,
        )

    @staticmethod
    def _ready_alert_fingerprint(
        *,
        symbol: str,
        action: str,
        entry_mode: str,
        entry: float,
        sl: float,
        tp: float,
        setup: Any = None,
    ) -> str:
        zone_key = ""
        if setup and getattr(setup, "zone", None):
            z = setup.zone
            zone_key = f"{float(z.low):.6g}:{float(z.high):.6g}"
        setup_ts = int(getattr(setup, "created_ts", 0) or 0) if setup else 0
        bos = float(getattr(setup, "bos_price", 0) or 0) if setup else 0.0
        return (
            f"{symbol.upper()}|{(action or '').upper()}|{(entry_mode or 'none').lower()}|"
            f"{round(float(entry), 6)}|{round(float(sl), 6)}|{round(float(tp), 6)}|"
            f"{setup_ts}|{round(bos, 6)}|{zone_key}"
        )

    def _is_duplicate_ready_alert(self, symbol: str, fingerprint: str) -> bool:
        """Block only repeats of the same setup/levels within dedupe window."""
        prev = self._last_ready_fingerprint.get(symbol)
        if not prev or prev != fingerprint:
            return False
        dedupe_sec = int(getattr(config.agent, "analyst_ready_dedupe_sec", 1800))
        if dedupe_sec <= 0:
            return False
        last_ts = int(self._last_ready_fingerprint_ts.get(symbol, 0))
        return (datetime.utcnow().timestamp() - last_ts) < dedupe_sec

    async def _maybe_send_structure_ready_alert(self, symbol: str, sc) -> None:
        if not sc or sc.phase != "ready":
            return
        import time as _time

        if _time.time() < getattr(self, "_ready_grace_until", 0):
            return
        if sc.entry_mode == "continuation" and not bool(
            getattr(config.agent, "agg_structure_continuation_enabled", False)
        ):
            return
        ready_min = int(getattr(config.agent, "analyst_ready_min_score", 78))
        min_win = float(getattr(config.agent, "analyst_min_win_probability", 0.50))
        if sc.entry_mode == "continuation":
            ready_min = int(
                getattr(config.agent, "analyst_continuation_ready_min_score", 76)
            )
            min_win = float(
                getattr(config.agent, "analyst_continuation_min_win_probability", 0.48)
            )
        if sc.quality_score < ready_min or sc.win_probability < min_win:
            return
        cap = int(getattr(config.agent, "analyst_max_alerts_per_day", 0))
        if cap > 0 and await self._alerts_remaining_today("ready") <= 0:
            return
        if await self.db.has_open_paper_position(symbol):
            return
        cooldown = int(getattr(config.agent, "analyst_ready_alert_cooldown_sec", 0))
        if cooldown > 0:
            last = self._last_ready_alert.get(symbol, 0)
            if datetime.utcnow().timestamp() - last < cooldown:
                return
        current = self._latest_close_sync(symbol)
        if not current:
            return

        setup = self.structure_gate.store.get(symbol)
        if not setup:
            return

        from core.candle_resample import load_candles_1m_sync, resample_ohlc
        from core.liquidity_targets import load_recent_book_zones_sync
        from core.smc_analysis import find_swings
        from core.strategy_engine import evaluate_ready_strategy

        cfg = self.structure_gate._config_from_agent()
        c1m = load_candles_1m_sync(self.db.db_path, symbol, limit=8000)
        ltf = resample_ohlc(c1m, cfg.ltf_minutes * 60)
        htf = resample_ohlc(c1m, cfg.htf_minutes * 60)
        vol = self._symbol_vol_pct_sync(symbol, int(datetime.utcnow().timestamp()))

        book_metrics: Optional[BookMetrics] = None
        flow_metrics = None
        stop_clusters: list = []
        book_zones = load_recent_book_zones_sync(self.db.db_path, symbol)
        if self.liquidity_agent:
            book_metrics = self.liquidity_agent.get_book_metrics(
                symbol, sc.aligned_action
            )
            stop_clusters = self.liquidity_agent.get_stop_clusters(symbol)
            for z in self.liquidity_agent.get_liquidity_levels_for_tp(
                symbol, sc.aligned_action, float(current)
            ):
                book_zones.append(z)
        if self.market_agent:
            flow_metrics = self.market_agent.get_trade_flow_metrics(
                symbol, sc.aligned_action
            )

        decision = evaluate_ready_strategy(
            setup=setup,
            action=sc.aligned_action,
            current_price=float(current),
            entry_mode=sc.entry_mode,
            db_path=self.db.db_path,
            symbol=symbol,
            book_metrics=book_metrics,
            flow_metrics=flow_metrics,
            ltf_swings=find_swings(ltf),
            htf_swings=find_swings(htf),
            stop_clusters=stop_clusters,
            book_zones=book_zones,
            volatility_pct=vol if vol > 0 else None,
            min_rr=float(getattr(config.agent, "agg_structure_min_rr", 2.5)),
            min_sl_pct=float(getattr(config.agent, "agg_structure_min_sl_pct", 0.55)),
            max_sl_pct=float(getattr(config.agent, "agg_structure_max_sl_pct", 2.5)),
            min_tp_pct=float(getattr(config.agent, "market_min_tp_room_pct", 1.2)),
            max_tp_pct=float(getattr(config.agent, "agg_tp_max_pct", 8.0)),
            require_book_aligned=bool(
                getattr(config.agent, "market_require_book_aligned", True)
            ),
            require_flow_aligned=bool(
                getattr(config.agent, "market_require_flow_aligned", True)
            ),
            zone_tol_pct=float(getattr(config.agent, "analyst_zone_tol_pct", 0.75)),
            recent_candles=c1m[-8:] if c1m else None,
        )
        if not decision.ok:
            self.logger.info(
                "Skip ready %s: %s",
                symbol,
                decision.block_reason or "strategy",
            )
            return

        fp = self._ready_alert_fingerprint(
            symbol=symbol,
            action=sc.aligned_action,
            entry_mode=sc.entry_mode,
            entry=decision.entry,
            sl=decision.sl,
            tp=decision.tp,
            setup=setup,
        )
        if self._is_duplicate_ready_alert(symbol, fp):
            self.logger.debug("Skip duplicate ready alert: %s", symbol)
            return

        sc.sl = decision.sl
        sc.tp = decision.tp
        sc.rr_ratio = decision.rr_ratio
        sc.checklist = {**(sc.checklist or {}), **decision.checklist}
        sc.reason = (
            f"{sc.one_liner or ''} | {decision.dominance_label} | {decision.exit_plan}"
        ).strip(" |")

        ok = await self._send_structure_telegram_alert(
            symbol=symbol,
            sc=sc,
            setup_phase="ready",
            entry_px=decision.entry,
            sl_px=decision.sl,
            tp_px=decision.tp,
            tp_source=decision.tp_source,
        )
        if ok:
            await self._record_alert_sent("ready")
            now_ts = int(datetime.utcnow().timestamp())
            self._last_ready_alert[symbol] = now_ts
            self._last_ready_fingerprint[symbol] = fp
            self._last_ready_fingerprint_ts[symbol] = now_ts
            await self._register_paper_trade(
                symbol=symbol,
                action=sc.aligned_action,
                entry=decision.entry,
                sl=decision.sl,
                tp=decision.tp,
                setup_quality=sc.quality_score,
                entry_mode=sc.entry_mode,
            )
            self.logger.info(
                "Ready setup alert sent: %s %s (q=%s, entry=%.6g, %s)",
                symbol,
                sc.aligned_action,
                sc.quality_score,
                decision.entry,
                decision.exit_plan,
            )

    async def _maybe_send_volume_spike_alert(self, symbol: str, setup=None, sc=None) -> None:
        if not bool(getattr(config.agent, "analyst_volume_spike_alerts_enabled", True)):
            return
        if not self.telegram_bot:
            return

        from core.candle_resample import load_candles_1m_sync
        from core.volume_spike import evaluate_tradable_volume_spike

        lookback = int(getattr(config.agent, "analyst_volume_spike_lookback", 20))
        threshold = float(getattr(config.agent, "volume_spike_threshold", 5.0))
        min_move = float(
            getattr(config.agent, "analyst_volume_spike_min_move_pct", 0.35)
        )
        min_range = float(
            getattr(config.agent, "analyst_volume_spike_min_range_pct", 0.40)
        )
        require_setup = bool(
            getattr(config.agent, "analyst_volume_spike_require_setup", True)
        )
        c1m = load_candles_1m_sync(self.db.db_path, symbol, limit=lookback + 5)
        tradable = evaluate_tradable_volume_spike(
            c1m,
            setup,
            lookback=lookback,
            min_ratio=threshold,
            min_price_move_pct=min_move,
            min_candle_range_pct=min_range,
            require_setup=require_setup,
        )
        if not tradable:
            return
        spike = tradable.metrics

        dedupe_sec = int(getattr(config.agent, "analyst_volume_spike_dedupe_sec", 3600))
        last_ts = self._last_volume_spike_alert.get(symbol, 0)
        last_ratio = self._last_volume_spike_ratio.get(symbol, 0.0)
        now = int(datetime.utcnow().timestamp())
        if dedupe_sec > 0 and now - last_ts < dedupe_sec:
            if spike.ratio < last_ratio * 1.25:
                return

        setup_state = tradable.setup_state
        setup_side = tradable.setup_side
        zone_low = zone_high = None
        quality = 0
        trade_hint = tradable.trade_hint
        if setup and getattr(setup, "zone", None):
            zone_low = float(setup.zone.low)
            zone_high = float(setup.zone.high)
        if sc:
            quality = int(sc.quality_score or 0)

        ok = await self.telegram_bot.send_volume_spike_alert(
            symbol=symbol,
            ratio=spike.ratio,
            price=spike.price,
            price_change_pct=spike.price_change_pct,
            direction=spike.direction,
            setup_state=setup_state,
            setup_side=setup_side,
            zone_low=zone_low,
            zone_high=zone_high,
            quality=quality,
            trade_hint=trade_hint,
            candle_range_pct=spike.candle_range_pct,
        )
        if ok:
            self._last_volume_spike_alert[symbol] = now
            self._last_volume_spike_ratio[symbol] = spike.ratio
            self.logger.info(
                "Volume spike alert: %s %.1fx (%s)",
                symbol,
                spike.ratio,
                spike.direction,
            )

    def _latest_close_sync(self, symbol: str) -> Optional[float]:
        import sqlite3

        try:
            conn = sqlite3.connect(self.db.db_path)
            cur = conn.cursor()
            for sym in (symbol, symbol.upper(), symbol.lower()):
                cur.execute(
                    """
                    SELECT close FROM candles
                    WHERE symbol = ? AND timeframe = '1m'
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (sym,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return float(row[0])
            return None
        except Exception:
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _symbol_vol_pct_sync(self, symbol: str, ts: int) -> float:
        from core.signal_features import _symbol_volatility_pct
        import sqlite3

        try:
            conn = sqlite3.connect(self.db.db_path)
            return _symbol_volatility_pct(conn, symbol, ts)
        except Exception:
            return 0.0
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _apply_trade_plan(
        self,
        action: Action,
        confidence: float,
        reasons: List[str],
        *,
        symbol: str,
        entry: Optional[float],
        price: Optional[float],
        sl: Optional[float],
        tp: Optional[float],
        structure_sl: Optional[float] = None,
        structure_tp: Optional[float] = None,
    ) -> tuple[Action, float, List[str], Optional[float], Optional[float], Optional[float]]:
        """Attach SL/TP with tight stop + RR take; block weak R:R setups."""
        if action not in (Action.BUY, Action.SELL):
            return action, confidence, reasons, entry, sl, tp

        entry_px = entry or price or self._latest_close_sync(symbol)
        if not entry_px or entry_px <= 0:
            return action, confidence, reasons, entry_px, sl, tp

        min_rr = float(getattr(config.agent, "agg_min_rr_ratio", 2.5))

        if structure_sl and structure_tp and structure_sl > 0 and structure_tp > 0:
            if action == Action.BUY:
                risk = entry_px - structure_sl
                reward = structure_tp - entry_px
            else:
                risk = structure_sl - entry_px
                reward = entry_px - structure_tp
            rr = reward / risk if risk > 0 else 0.0
            if rr >= min_rr:
                reasons = reasons + [
                    f"Structural SL/TP: SL={structure_sl:.4f} TP={structure_tp:.4f} (RR 1:{rr:.1f})."
                ]
                return (
                    action,
                    confidence,
                    reasons,
                    float(entry_px),
                    float(structure_sl),
                    float(structure_tp),
                )

        sl_pct = float(getattr(config.agent, "agg_sl_pct", 0.35))
        tp_rr = float(getattr(config.agent, "agg_tp_rr_ratio", 3.0))
        min_rr = float(getattr(config.agent, "agg_min_rr_ratio", 2.5))
        vol = self._symbol_vol_pct_sync(symbol, int(datetime.utcnow().timestamp()))

        if sl is None or tp is None:
            try:
                levels = compute_trade_levels(
                    float(entry_px),
                    action.value,
                    sl_pct=sl_pct,
                    tp_rr_ratio=tp_rr,
                    volatility_pct=vol if vol > 0 else None,
                )
                sl = levels.sl
                tp = levels.tp
                if not passes_min_rr(levels.sl_pct, levels.tp_pct, min_rr=min_rr):
                    return (
                        Action.WAIT,
                        min(confidence, 0.40),
                        reasons
                        + [
                            f"RR plan gate: {levels.sl_pct:.2f}% SL / {levels.tp_pct:.2f}% TP "
                            f"(1:{levels.rr_ratio:.1f}) < min 1:{min_rr:.1f}."
                        ],
                        float(entry_px),
                        sl,
                        tp,
                    )
                reasons = reasons + [
                    f"Trade plan: SL {levels.sl_pct:.2f}% / TP {levels.tp_pct:.2f}% "
                    f"(RR 1:{levels.rr_ratio:.1f}, BE WR ~{100 * breakeven_win_rate(levels.rr_ratio):.0f}%)."
                ]
            except Exception as exc:
                self.logger.warning("Trade plan failed for %s: %s", symbol, exc)

        return action, confidence, reasons, float(entry_px), sl, tp

    def _signal_confidence(self, signal: Signal) -> float:
        """Best-effort per-signal confidence used by score aggregator."""
        data_conf = None
        if signal.data:
            try:
                data_conf = float(signal.data.get("confidence"))
            except (TypeError, ValueError):
                data_conf = None
        if data_conf is not None:
            return max(0.0, min(1.0, data_conf))
        return float(self.priority_weights.get(signal.priority, 0.5))

    def _classify_signal(self, signal: Signal) -> str:
        """
        Conservative signal side classifier.
        Returns: buy | sell | exit | neutral
        """
        st = (signal.signal_type or "").lower()
        data = signal.data or {}
        msg = (signal.message or "").lower()

        if any(k in st for k in ("liquidity_crisis", "dump_danger", "rapid_dump")):
            return "exit"
        if any(k in st for k in ("exit", "danger", "crisis")):
            return "exit"
        if any(k in st for k in ("support_break", "sell", "dump")):
            return "sell"
        if any(k in st for k in ("resistance_break", "pump", "buy", "whale_activity")):
            return "buy"

        if "imbalance" in st:
            try:
                imbalance = float(data.get("imbalance", 0.0))
                if imbalance > 0:
                    return "buy"
                if imbalance < 0:
                    return "sell"
            except (TypeError, ValueError):
                pass

        if any(k in st for k in ("volume_spike", "price_spike", "high_volatility")):
            if any(k in msg for k in ("dump", "sell", "down", "bear")):
                return "sell"
            if any(k in msg for k in ("pump", "buy", "up", "bull")):
                return "buy"
            return "neutral"

        return "neutral"

    def _apply_strategy_mode(
        self,
        action: Action,
        confidence: float,
        buy_signals: List[Signal],
        sell_signals: List[Signal],
        exit_signals: List[Signal],
        reasons: List[str],
    ) -> tuple[Action, float, List[str]]:
        """
        Strategy layer on top of raw weighted scores.
        - balanced: default behavior
        - trend_following: require market confirmation for BUY/SELL
        - defensive: strongly prefer WAIT unless broad confirmation
        """
        mode = getattr(config.agent, "strategy_mode", "balanced").lower()
        min_conf = float(getattr(config.agent, "strategy_min_confidence", 0.55))
        req_confirms = int(getattr(config.agent, "strategy_required_confirmations", 2))

        def _agent_count(items: List[Signal]) -> int:
            return len({s.agent_type for s in items})

        buy_confirms = _agent_count(buy_signals)
        sell_confirms = _agent_count(sell_signals)
        has_market_buy = any(s.agent_type == "market" for s in buy_signals)
        has_market_sell = any(s.agent_type == "market" for s in sell_signals)
        heavy_exit = len(exit_signals) >= max(2, req_confirms)
        bearish_guard_on = bool(
            getattr(config.agent, "strategy_bearish_guard_enabled", True)
        )
        bearish_threshold = int(
            getattr(config.agent, "strategy_bearish_guard_threshold", 2)
        )
        bearish_pressure = 0
        for s in exit_signals + sell_signals:
            st = (s.signal_type or "").lower()
            if any(k in st for k in ("dump", "danger", "crisis", "sell", "support_break")):
                bearish_pressure += 1
            if s.agent_type == "emergency":
                bearish_pressure += 1

        if bearish_guard_on and action == Action.BUY and bearish_pressure >= bearish_threshold:
            if bearish_pressure >= bearish_threshold + 2:
                return Action.EXIT, min(confidence, 0.55), reasons + [
                    f"Bearish guard: high sell pressure ({bearish_pressure})."
                ]
            return Action.WAIT, min(confidence, 0.40), reasons + [
                f"Bearish guard: buy blocked ({bearish_pressure})."
            ]

        buy_score_est = self._calculate_score(buy_signals)
        sell_score_est = self._calculate_score(sell_signals)
        if (
            bool(getattr(config.agent, "agg_bearish_regime_enabled", True))
            and action == Action.BUY
            and is_bearish_regime(
                buy_count=len(buy_signals),
                sell_count=len(sell_signals),
                exit_count=len(exit_signals),
                emergency_count=sum(1 for s in buy_signals + sell_signals + exit_signals if s.agent_type == "emergency"),
                bearish_pressure=bearish_pressure,
                buy_score=buy_score_est,
                sell_score=sell_score_est,
            )
        ):
            return Action.WAIT, min(confidence, 0.38), reasons + [bearish_regime_reason()]

        if mode == "trend_following":
            if action == Action.BUY:
                if confidence < min_conf or buy_confirms < req_confirms or not has_market_buy:
                    return Action.WAIT, min(confidence, 0.45), reasons + [
                        "Trend mode: buy blocked (weak confirmation)."
                    ]
            if action == Action.SELL:
                if confidence < min_conf or sell_confirms < req_confirms or not has_market_sell:
                    return Action.WAIT, min(confidence, 0.45), reasons + [
                        "Trend mode: sell blocked (weak confirmation)."
                    ]

        elif mode == "defensive":
            if action in (Action.BUY, Action.SELL):
                confirms = buy_confirms if action == Action.BUY else sell_confirms
                sell_bearish_ok = (
                    action == Action.SELL
                    and bearish_pressure >= 2
                    and sell_score_est > buy_score_est
                )
                min_conf_req = min_conf if sell_bearish_ok else (min_conf + 0.05)
                min_confirms_req = req_confirms if sell_bearish_ok else (req_confirms + 1)
                if confidence < min_conf_req or confirms < min_confirms_req:
                    return Action.WAIT, min(confidence, 0.40), reasons + [
                        "Defensive mode: waiting for stronger multi-agent confluence."
                    ]
            if action == Action.BUY and heavy_exit:
                return Action.WAIT, min(confidence, 0.35), reasons + [
                    "Defensive mode: elevated exit pressure detected."
                ]

        # balanced or unknown
        return action, confidence, reasons

    def _passes_ev_gate(
        self,
        *,
        action: Action,
        confidence: float,
        margin: float,
        source_count: int,
        bearish_pressure: int,
        emergency_count: int,
        buy_count: int,
        sell_count: int,
    ) -> tuple[bool, str]:
        """EV + R:R gate (expected edge must cover costs + min profit bps)."""
        agent = config.agent
        cal_extra, cal_note = calibration_extra_bps(
            self.edge_calibration,
            action=action.value,
            confidence=confidence,
            enabled=bool(getattr(agent, "agg_edge_calibration_enabled", True)),
        )
        result = evaluate_edge_gate(
            action=action.value,
            confidence=confidence,
            margin=margin,
            source_count=source_count,
            bearish_pressure=bearish_pressure,
            emergency_count=emergency_count,
            buy_count=buy_count,
            sell_count=sell_count,
            fee_bps_per_side=float(getattr(agent, "ev_fee_bps_per_side", 2.0)),
            slippage_bps=float(getattr(agent, "ev_slippage_bps", 3.0)),
            buffer_bps=float(getattr(agent, "ev_buffer_bps", 6.0)),
            min_profit_bps=float(getattr(agent, "agg_rr_min_profit_bps", 15.0)),
            ev_gate_enabled=bool(getattr(agent, "ev_gate_enabled", True)),
            rr_gate_enabled=bool(getattr(agent, "agg_rr_gate_enabled", True)),
            calibration_extra_bps=cal_extra,
            confidence_mult=float(getattr(agent, "ev_confidence_mult", 16.0)),
            margin_mult=float(getattr(agent, "ev_margin_mult", 20.0)),
            source_mult=float(getattr(agent, "ev_source_mult", 3.0)),
            bearish_penalty_mult=float(getattr(agent, "ev_bearish_penalty_mult", 6.0)),
            emergency_penalty_mult=float(
                getattr(agent, "ev_emergency_penalty_mult", 4.0)
            ),
            conflict_penalty_mult=float(
                getattr(agent, "ev_conflict_penalty_mult", 25.0)
            ),
        )
        reason = result.reason
        if not result.passed and cal_note:
            reason = f"{reason} {cal_note}".strip()
        return result.passed, reason

    async def start(self):
        """Start background aggregation tasks."""
        self.running = True
        await asyncio.gather(
            self._collect_signals(),
            self._process_aggregation(),
            self._structure_scan_loop(),
            self._send_periodic_reports(),
        )

    async def _structure_scan_loop(self):
        """Keep SMC setups fresh; in analyst mode alert on forming setups."""
        while self.running:
            try:
                analyst = bool(getattr(config.agent, "analyst_mode_enabled", True))
                forming_min = int(getattr(config.agent, "analyst_forming_min_score", 58))
                forming_only_retest = bool(
                    getattr(config.agent, "analyst_forming_only_retest", True)
                )
                if bool(getattr(config.agent, "agg_structure_gate_enabled", True)):
                    for symbol in list(config.default_symbols):
                        if not self.running:
                            break
                        try:
                            self.structure_gate.scan_symbol(self.db.db_path, symbol)
                            if analyst and self.telegram_bot:
                                book_metrics = None
                                setup = self.structure_gate.store.get(symbol)
                                if self.liquidity_agent and setup:
                                    act = (
                                        "BUY"
                                        if (setup.side or "").lower() == "long"
                                        else "SELL"
                                    )
                                    book_metrics = self.liquidity_agent.get_book_metrics(
                                        symbol, act
                                    )
                                sc = self.structure_gate.score_symbol(
                                    self.db.db_path,
                                    symbol,
                                    book_metrics=book_metrics,
                                )
                                if sc:
                                    await self._maybe_send_structure_ready_alert(symbol, sc)
                                await self._maybe_send_volume_spike_alert(
                                    symbol, setup=setup, sc=sc
                                )
                                if (
                                    sc
                                    and sc.phase == "forming"
                                    and bool(
                                        getattr(
                                            config.agent,
                                            "analyst_forming_alerts_enabled",
                                            False,
                                        )
                                    )
                                    and sc.quality_score >= forming_min
                                    and (
                                        not forming_only_retest
                                        or sc.checklist.get("setup_state")
                                        == "await_retest"
                                    )
                                ):
                                    last = self._last_forming_alert.get(symbol, 0)
                                    if (
                                        datetime.utcnow().timestamp() - last
                                        > 3600
                                        and await self._alerts_remaining_today("forming") > 0
                                    ):
                                        ok = await self._send_structure_telegram_alert(
                                            symbol=symbol,
                                            sc=sc,
                                            setup_phase="forming",
                                        )
                                        if ok:
                                            await self._record_alert_sent("forming")
                                            self._last_forming_alert[symbol] = int(
                                                datetime.utcnow().timestamp()
                                            )
                        except Exception as exc:
                            self.logger.debug("Structure scan %s: %s", symbol, exc)
                if self.event_router:
                    self.event_router.ping_health("aggregator")
                await asyncio.sleep(
                    int(getattr(config.agent, "agg_structure_scan_interval_sec", 60))
                )
            except Exception as exc:
                self.logger.warning("Structure scan loop: %s", exc)
                await asyncio.sleep(30)

    async def _collect_signals(self):
        """Placeholder loop; signals arrive via add_signal from EventRouter."""
        while self.running:
            await asyncio.sleep(1)

    async def add_signal(self, signal: Signal):
        """Append a signal from another agent (bounded per symbol)."""
        if signal.symbol:
            self.signals_by_symbol[signal.symbol].append(signal)
            if len(self.signals_by_symbol[signal.symbol]) > 50:
                self.signals_by_symbol[signal.symbol] = self.signals_by_symbol[signal.symbol][-50:]

    async def _process_aggregation(self):
        """Aggregate recent signals and emit consolidated alerts."""
        while self.running:
            try:
                await asyncio.sleep(config.agent.aggregation_interval)
                
                for symbol, signals in list(self.signals_by_symbol.items()):
                    if not signals:
                        continue
                    
                    recent_signals = [
                        s
                        for s in signals
                        if (datetime.utcnow() - s.timestamp).total_seconds()
                        < config.agent.recent_signals_window
                    ]

                    if not recent_signals:
                        continue

                    if is_stable_coin(symbol, self.stable_coins):
                        self.logger.debug("Skipping stable token: %s", symbol)
                        continue

                    if len(symbol) < 6:
                        continue

                    aggregated = await self._aggregate_signals(symbol, recent_signals)

                    if aggregated and aggregated.confidence >= config.agent.min_confidence:
                        if aggregated.price is not None and aggregated.price <= 0:
                            self.logger.debug(
                                "Skipping %s: invalid price %s", symbol, aggregated.price
                            )
                            continue

                        signal_key = (symbol, aggregated.action.value)
                        last_sent = self.last_sent_signals.get(signal_key, 0)
                        if last_sent:
                            from datetime import datetime as dt

                            last_sent_dt = dt.fromtimestamp(last_sent)
                            time_since_last = (
                                datetime.utcnow() - last_sent_dt
                            ).total_seconds()
                        else:
                            time_since_last = 999

                        if time_since_last < config.agent.signal_deduplication_window:
                            self.logger.debug(
                                "Skipping duplicate: %s %s",
                                symbol,
                                aggregated.action.value,
                            )
                            continue

                        sent_telegram = False
                        if self.telegram_bot and await self._should_send_telegram(
                            aggregated
                        ):
                            kind = (
                                "ready"
                                if aggregated.action in (Action.BUY, Action.SELL)
                                else "risk"
                            )
                            sent_telegram = await self._send_aggregated_signal(aggregated)
                            if sent_telegram:
                                await self._record_alert_sent(kind)
                                if kind == "ready":
                                    await self._register_paper_trade(
                                        symbol=aggregated.symbol,
                                        action=aggregated.action.value,
                                        entry=aggregated.entry or aggregated.price,
                                        sl=aggregated.sl,
                                        tp=aggregated.tp,
                                        setup_quality=aggregated.setup_quality,
                                        entry_mode=aggregated.entry_mode,
                                    )
                                self.metrics.record_signal(
                                    "aggregator",
                                    aggregated.action.value.lower(),
                                    symbol,
                                )

                        self.last_sent_signals[signal_key] = (
                            datetime.utcnow().timestamp()
                        )

                        await self._save_aggregated_signal(
                            aggregated,
                            sent_telegram=sent_telegram,
                        )

                if self.event_router:
                    self.event_router.ping_health("aggregator")

            except Exception as e:
                self.logger.error("Aggregation error: %s", e, exc_info=True)
                self.metrics.record_error()
                await asyncio.sleep(5)
    
    async def _aggregate_signals(self, symbol: str, signals: List[Signal]) -> Optional[AggregatedSignal]:
        """Combine raw signals into a single AggregatedSignal."""
        try:
            buy_signals = []
            sell_signals = []
            exit_signals = []

            for signal in signals:
                cls = self._classify_signal(signal)
                if cls == "buy":
                    buy_signals.append(signal)
                elif cls == "sell":
                    sell_signals.append(signal)
                    if signal.agent_type == "emergency":
                        exit_signals.append(signal)
                elif cls == "exit":
                    exit_signals.append(signal)
            bearish_pressure = 0
            emergency_count = 0
            for s in signals:
                if s.agent_type == "emergency":
                    emergency_count += 1
                st = (s.signal_type or "").lower()
                if any(
                    k in st
                    for k in (
                        "dump",
                        "danger",
                        "crisis",
                        "sell",
                        "support_break",
                        "exit",
                    )
                ):
                    bearish_pressure += 1
            
            buy_score = self._calculate_score(buy_signals)
            sell_score = self._calculate_score(sell_signals)
            exit_score = self._calculate_score(exit_signals)

            max_score = max(buy_score, sell_score, exit_score)
            sorted_scores = sorted([buy_score, sell_score, exit_score], reverse=True)
            score_margin = sorted_scores[0] - sorted_scores[1]
            min_margin = float(getattr(config.agent, "agg_min_margin", 0.12))
            min_score = float(getattr(config.agent, "agg_min_score", 0.35))

            if max_score < min_score:
                return None
            if score_margin < min_margin:
                action = Action.WAIT
                confidence = max_score
                reasons = ["Low directional edge: conflicting signal groups."]
            elif exit_score >= max_score * 0.9:
                action = Action.EXIT
                confidence = exit_score
                reasons = self._extract_reasons(exit_signals)
            elif sell_score > buy_score:
                action = Action.SELL
                confidence = sell_score
                reasons = self._extract_reasons(sell_signals)
            elif buy_score > 0:
                action = Action.BUY
                confidence = buy_score
                reasons = self._extract_reasons(buy_signals)
            else:
                action = Action.WAIT
                confidence = 0.0
                reasons = []

            action, confidence, reasons = self._apply_strategy_mode(
                action,
                confidence,
                buy_signals,
                sell_signals,
                exit_signals,
                reasons,
            )
            ev_ok, ev_reason = self._passes_ev_gate(
                action=action,
                confidence=confidence,
                margin=score_margin,
                source_count=len(signals),
                bearish_pressure=bearish_pressure,
                emergency_count=emergency_count,
                buy_count=len(buy_signals),
                sell_count=len(sell_signals),
            )
            if not ev_ok:
                action = Action.WAIT
                confidence = min(confidence, 0.45)
                reasons = reasons + [ev_reason or "EV gate: expected edge below costs."]

            btc_snap = {"trend": "unknown", "return_pct": None}
            if bool(getattr(config.agent, "agg_btc_trend_filter_enabled", True)):
                now_ts = int(datetime.utcnow().timestamp())
                btc_snap = btc_trend_at_ts(
                    self.db.db_path,
                    now_ts,
                    lookback_minutes=int(
                        getattr(config.agent, "agg_btc_trend_lookback_minutes", 30)
                    ),
                    symbol=str(getattr(config.agent, "agg_btc_symbol", "BTCUSDT")),
                    down_threshold_pct=float(
                        getattr(config.agent, "agg_btc_trend_down_threshold_pct", -0.08)
                    ),
                    up_threshold_pct=float(
                        getattr(config.agent, "agg_btc_trend_up_threshold_pct", 0.08)
                    ),
                )

            ok, q_reason = passes_entry_quality(
                action.value,
                confidence,
                buy_signals,
                sell_signals,
                min_unique_agents=int(
                    getattr(config.agent, "agg_min_unique_agents", 2)
                ),
                min_directional_confidence=float(
                    getattr(config.agent, "agg_directional_min_confidence", 0.58)
                ),
                require_quality_agent_for_buy=bool(
                    getattr(config.agent, "agg_require_quality_agent_for_buy", True)
                ),
                require_quality_agent_for_sell=bool(
                    getattr(config.agent, "agg_require_quality_agent_for_sell", True)
                ),
                exit_signals=exit_signals,
                emergency_count=emergency_count,
                bearish_pressure=bearish_pressure,
                buy_score=buy_score,
                sell_score=sell_score,
                bearish_regime_enabled=bool(
                    getattr(config.agent, "agg_bearish_regime_enabled", True)
                ),
                symbol=symbol,
                btc_trend=str(btc_snap.get("trend") or "unknown"),
                btc_trend_return_pct=btc_snap.get("return_pct"),
                btc_trend_filter_enabled=bool(
                    getattr(config.agent, "agg_btc_trend_filter_enabled", True)
                ),
            )
            if not ok:
                action = Action.WAIT
                confidence = min(confidence, 0.42)
                reasons = reasons + [q_reason]

            risk = self._calculate_risk(signals)

            price = None
            entry = None
            sl = None
            tp = None

            for signal in signals:
                if signal.data:
                    if 'price' in signal.data and price is None:
                        price_val = signal.data['price']
                        try:
                            price_val = float(price_val)
                            if price_val > 0:
                                price = price_val
                        except (ValueError, TypeError):
                            pass
                    if 'entry' in signal.data and entry is None:
                        try:
                            entry_val = float(signal.data['entry'])
                            if entry_val > 0:
                                entry = entry_val
                        except (ValueError, TypeError):
                            pass
                    if 'sl' in signal.data and sl is None:
                        try:
                            sl_val = float(signal.data['sl'])
                            if sl_val > 0:
                                sl = sl_val
                        except (ValueError, TypeError):
                            pass
                    if 'tp' in signal.data and tp is None:
                        try:
                            tp_val = float(signal.data['tp'])
                            if tp_val > 0:
                                tp = tp_val
                        except (ValueError, TypeError):
                            pass

            ml_enabled = bool(getattr(config.agent, "agg_ml_gate_enabled", True))
            ml_min_prob = float(getattr(config.agent, "agg_ml_min_win_prob", 0.42))
            analyst_mode = bool(getattr(config.agent, "analyst_mode_enabled", True))
            ml_ok, ml_prob, ml_reason = self.signal_model_gate.evaluate(
                db_path=self.db.db_path,
                signal_ts=int(datetime.utcnow().timestamp()),
                symbol=symbol,
                action=action.value,
                confidence=confidence,
                risk=risk.value,
                reasons=reasons,
                source_signals_count=len(signals),
                margin_est=score_margin,
                unique_agents_est=len({s.agent_type for s in signals}),
                min_win_prob=ml_min_prob,
                enabled=ml_enabled and not analyst_mode,
            )
            if not ml_ok and not analyst_mode:
                action = Action.WAIT
                confidence = min(confidence, 0.40)
                reasons = reasons + [ml_reason]
            elif ml_prob > 0 and ml_enabled and self.signal_model_gate.ready:
                confidence = min(1.0, confidence * 0.85 + ml_prob * 0.15)

            structure_sl = structure_tp = None
            struct_enabled = bool(getattr(config.agent, "agg_structure_gate_enabled", True))
            entry_for_struct = entry or price or self._latest_close_sync(symbol)
            ev_passed = True
            setup_score = None

            if analyst_mode and struct_enabled and action in (Action.BUY, Action.SELL):
                setup_score = self.structure_gate.score_at(
                    db_path=self.db.db_path,
                    symbol=symbol,
                    action=action.value,
                    entry_price=entry_for_struct,
                    aggregator_confidence=confidence,
                    ml_win_prob=ml_prob if ml_prob > 0 else 0.0,
                    ev_passed=ev_passed,
                )
                ready_min = int(getattr(config.agent, "analyst_ready_min_score", 72))
                min_win = float(getattr(config.agent, "analyst_min_win_probability", 0.45))
                cont_min = int(
                    getattr(config.agent, "analyst_continuation_ready_min_score", 76)
                )
                cont_win = float(
                    getattr(config.agent, "analyst_continuation_min_win_probability", 0.48)
                )
                score_min = (
                    cont_min
                    if setup_score.entry_mode == "continuation"
                    else ready_min
                )
                win_min = cont_win if setup_score.entry_mode == "continuation" else min_win

                if (
                    setup_score.phase == "ready"
                    and setup_score.quality_score >= score_min
                    and setup_score.win_probability >= win_min
                ):
                    structure_sl = setup_score.sl
                    structure_tp = setup_score.tp
                    if setup_score.one_liner:
                        reasons = reasons + [setup_score.one_liner]
                    confidence = min(
                        1.0,
                        confidence * 0.6
                        + (setup_score.quality_score / 100.0) * 0.25
                        + setup_score.win_probability * 0.15,
                    )
                else:
                    action = Action.WAIT
                    confidence = min(confidence, 0.42)
                    reasons = reasons + [
                        f"Analyst: качество {setup_score.quality_score}/100, "
                        f"фаза {setup_score.phase}, "
                        f"шанс {setup_score.win_probability:.0%} — {setup_score.reason}"
                    ]
            elif struct_enabled and action in (Action.BUY, Action.SELL):
                struct_res = self.structure_gate.evaluate(
                    db_path=self.db.db_path,
                    symbol=symbol,
                    action=action.value,
                    entry_price=entry_for_struct,
                    enabled=True,
                )
                if not struct_res.allowed:
                    action = Action.WAIT
                    confidence = min(confidence, 0.38)
                    reasons = reasons + [struct_res.reason or "Structure gate blocked."]
                else:
                    structure_sl = struct_res.sl
                    structure_tp = struct_res.tp
                    if struct_res.one_liner:
                        reasons = reasons + [struct_res.one_liner]

            action, confidence, reasons, entry, sl, tp = self._apply_trade_plan(
                action,
                confidence,
                reasons,
                symbol=symbol,
                entry=entry,
                price=price,
                sl=sl,
                tp=tp,
                structure_sl=structure_sl,
                structure_tp=structure_tp,
            )
            if entry and not price:
                price = entry

            aggregated = AggregatedSignal(
                symbol=symbol,
                action=action,
                risk=risk,
                confidence=confidence,
                reasons=reasons,
                price=price,
                entry=entry,
                sl=sl,
                tp=tp
            )
            aggregated.source_signals = signals
            if setup_score is not None:
                aggregated.setup_quality = setup_score.quality_score
                aggregated.setup_phase = setup_score.phase
                aggregated.win_probability = setup_score.win_probability
                aggregated.entry_mode = setup_score.entry_mode

            baseline_action = aggregated.action.value
            council_on = getattr(config.agent, "expert_council_enabled", True)
            if council_on:
                refine_aggregate(
                    aggregated,
                    signals,
                    self.logger,
                    enabled=True,
                    disagreement_threshold=getattr(
                        config.agent, "expert_council_disagreement_threshold", 0.45
                    ),
                    disagreement_penalty=getattr(
                        config.agent, "expert_council_disagreement_penalty", 0.35
                    ),
                )
            aggregated.baseline_action = baseline_action

            return aggregated
            
        except Exception as e:
            self.logger.error("Aggregation failed for %s: %s", symbol, e, exc_info=True)
            return None
    
    def _calculate_score(self, signals: List[Signal]) -> float:
        """Weighted score for a group of signals."""
        if not signals:
            return 0.0

        weighted_conf_sum = 0.0
        total_weight = 0.0
        unique_agents = set()

        for signal in signals:
            agent_type = signal.agent_type.lower()
            signal_type = signal.signal_type.lower()

            type_weight = float(self.signal_weights.get(agent_type, {}).get(signal_type, 0.45))
            priority_weight = float(self.priority_weights.get(signal.priority, 0.5))
            agent_mult = float(self.agent_weight_mult.get(agent_type, 1.0))
            weight = max(0.05, type_weight * (0.5 + 0.5 * priority_weight) * agent_mult)
            signal_conf = self._signal_confidence(signal)

            total_weight += weight
            weighted_conf_sum += weight * signal_conf
            unique_agents.add(agent_type)

        if total_weight > 0:
            score = max(0.0, min(weighted_conf_sum / total_weight, 1.0))
        else:
            score = 0.0

        if len(unique_agents) > 1:
            consensus_bonus = min((len(unique_agents) - 1) * 0.04, 0.16)
            score = min(score + consensus_bonus, 1.0)

        return score
    
    def _extract_reasons(self, signals: List[Signal]) -> List[str]:
        """Build short human-readable reasons (English) from signals."""
        reasons = []
        seen_reasons = set()

        for signal in signals:
            reason = None

            if signal.data and "reason" in signal.data:
                reason = signal.data["reason"]
            else:
                msg_lower = signal.message.lower()
                if "пробой" in msg_lower or "break" in msg_lower:
                    reason = f"Level break ({signal.signal_type})"
                elif "объем" in msg_lower or "volume" in msg_lower:
                    if signal.data and "volume_spike" in signal.data:
                        spike = signal.data.get("volume_spike", 0)
                        reason = f"Volume spike +{spike:.1f}x"
                    else:
                        reason = "Volume spike"
                elif "кит" in msg_lower or "whale" in msg_lower:
                    if signal.data and "volume_usd" in signal.data:
                        volume = signal.data["volume_usd"]
                        reason = f"Whale-sized flow ${volume:,.0f}"
                    else:
                        reason = "Whale activity"
                elif "имбаланс" in msg_lower or "imbalance" in msg_lower:
                    if signal.data and "imbalance" in signal.data:
                        imb = signal.data["imbalance"]
                        direction = "bids" if imb > 0 else "asks"
                        reason = f"Book imbalance {abs(imb):.1%} ({direction})"
                    else:
                        reason = "Order book imbalance"
                elif "дамп" in msg_lower or "dump" in msg_lower:
                    reason = "Dump risk"
                elif "памп" in msg_lower or "pump" in msg_lower:
                    reason = "Pump potential"
                else:
                    reason = f"{signal.agent_type}: {signal.signal_type}"

            if reason and reason not in seen_reasons:
                reasons.append(reason)
                seen_reasons.add(reason)

        return reasons[:5]
    
    def _calculate_risk(self, signals: List[Signal]) -> RiskLevel:
        """Derive coarse risk from priorities and agent mix."""
        risk_score = 0.0

        for signal in signals:
            if signal.priority in [Priority.CRITICAL, Priority.URGENT]:
                risk_score += 0.5
            elif signal.priority == Priority.HIGH:
                risk_score += 0.3

            if signal.agent_type == "emergency":
                risk_score += 0.4

            if signal.agent_type == "shitcoin":
                if signal.data and 'risk' in signal.data:
                    risk_score += signal.data['risk']
                else:
                    risk_score += 0.5
        
        risk_score = min(risk_score, 1.0)
        
        if risk_score > 0.7:
            return RiskLevel.HIGH
        elif risk_score > 0.4:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    async def _should_send_telegram(self, aggregated: AggregatedSignal) -> bool:
        """Telegram from aggregation loop (disabled when structure-only analyst mode)."""
        analyst = bool(getattr(config.agent, "analyst_mode_enabled", True))
        if analyst and bool(
            getattr(config.agent, "analyst_structure_only_telegram", True)
        ):
            return False

        act = aggregated.action

        if analyst and act in (Action.BUY, Action.SELL):
            ready_min = int(getattr(config.agent, "analyst_ready_min_score", 78))
            min_win = float(getattr(config.agent, "analyst_min_win_probability", 0.50))
            if aggregated.entry_mode == "continuation":
                ready_min = int(
                    getattr(config.agent, "analyst_continuation_ready_min_score", 76)
                )
                min_win = float(
                    getattr(config.agent, "analyst_continuation_min_win_probability", 0.48)
                )
            return (
                aggregated.setup_phase == "ready"
                and aggregated.setup_quality >= ready_min
                and aggregated.win_probability >= min_win
                and (
                    int(getattr(config.agent, "analyst_max_alerts_per_day", 0)) <= 0
                    or await self._alerts_remaining_today("ready") > 0
                )
            )

        if act in (Action.BUY, Action.SELL):
            return True
        if act == Action.EXIT and aggregated.confidence >= 0.82:
            if analyst and not bool(
                getattr(config.agent, "analyst_exit_alerts_enabled", False)
            ):
                return False
            open_pos = await self.db.has_open_paper_position(aggregated.symbol)
            if not open_pos:
                return False
            last = self._last_exit_alert.get(aggregated.symbol, 0)
            if datetime.utcnow().timestamp() - last < 14400:
                return False
            self._last_exit_alert[aggregated.symbol] = int(
                datetime.utcnow().timestamp()
            )
            return True
        return False

    def _reset_alert_day_if_needed(self) -> None:
        day = datetime.utcnow().strftime("%Y-%m-%d")
        if day != self._alert_day_key:
            self._alert_day_key = day
            self._forming_alerts_today = 0
            self._ready_alerts_today = 0

    async def _alerts_remaining_today(self, kind: str) -> int:
        day = datetime.utcnow().strftime("%Y-%m-%d")
        self._alert_day_key = day
        if kind == "ready":
            cap = int(getattr(config.agent, "analyst_max_alerts_per_day", 0))
        else:
            cap = int(
                getattr(config.agent, "analyst_max_forming_alerts_per_day", 0)
            )
        if cap <= 0:
            return 999_999
        counts = await self.db.get_analyst_alert_counts(day)
        if kind == "ready":
            used = counts.get("ready", 0)
        else:
            used = counts.get("forming", 0)
        return max(0, cap - used)

    async def _record_alert_sent(self, kind: str) -> None:
        day = datetime.utcnow().strftime("%Y-%m-%d")
        self._alert_day_key = day
        await self.db.increment_analyst_alert_count(day, kind)
        if kind == "ready":
            self._ready_alerts_today += 1
        else:
            self._forming_alerts_today += 1

    async def _send_aggregated_signal(self, aggregated: AggregatedSignal) -> bool:
        """Push trade/risk alert with chart to Telegram."""
        if not self.telegram_bot:
            return False
        try:
            from core.trade_chart import build_trade_chart_bytes

            is_risk = aggregated.action == Action.EXIT
            chart_bytes = None
            if not is_risk:
                entry = aggregated.entry or aggregated.price
                loop = asyncio.get_event_loop()
                cfg = self.structure_gate._config_from_agent()
                chart_bytes = await loop.run_in_executor(
                    None,
                    lambda: build_trade_chart_bytes(
                        self.db.db_path,
                        aggregated.symbol,
                        action=aggregated.action.value,
                        entry=entry,
                        sl=aggregated.sl,
                        tp=aggregated.tp,
                        bar_minutes=int(cfg.ltf_minutes),
                        bars=96,
                    ),
                )

            return await self.telegram_bot.send_trade_alert(
                symbol=aggregated.symbol,
                action=aggregated.action.value,
                entry=aggregated.entry or aggregated.price,
                sl=aggregated.sl,
                tp=aggregated.tp,
                confidence=aggregated.confidence,
                risk=aggregated.risk.value,
                reasons=aggregated.reasons,
                chart_bytes=chart_bytes,
                is_risk_alert=is_risk,
                setup_quality=aggregated.setup_quality,
                setup_phase=aggregated.setup_phase,
                win_probability=aggregated.win_probability,
                entry_mode=aggregated.entry_mode,
            )
        except Exception as e:
            self.logger.error("Failed to send aggregated signal: %s", e, exc_info=True)
            return False

    def _format_aggregated_message(self, aggregated: AggregatedSignal) -> str:
        """HTML body for Telegram."""
        action_emoji = {
            Action.BUY: "🟢",
            Action.SELL: "🔴",
            Action.EXIT: "⚠️",
            Action.WAIT: "⏸️"
        }
        
        risk_emoji = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🔴"
        }
        
        emoji = action_emoji.get(aggregated.action, "📊")
        risk_icon = risk_emoji.get(aggregated.risk, "⚪")
        
        priority_text = (
            "URGENT"
            if aggregated.confidence > 0.8
            else "HIGH"
            if aggregated.confidence > 0.6
            else "MEDIUM"
        )
        header = f"{emoji} <b>{priority_text} {aggregated.action.value}</b>"
        if aggregated.symbol:
            header += f"\n{aggregated.symbol}"
            if aggregated.price:
                header += f" @ {aggregated.price:.4f}"
        
        message = header
        
        message += f"\n\n📊 <b>Confidence:</b> {aggregated.confidence:.1%}"
        message += f"\n{risk_icon} <b>Risk:</b> {aggregated.risk.value}"

        if aggregated.reasons:
            message += "\n\n<b>Reasons:</b>"
            for reason in aggregated.reasons:
                message += f"\n  • {reason}"

        if aggregated.entry or aggregated.sl or aggregated.tp:
            message += "\n\n<b>Levels:</b>"
            if aggregated.entry:
                message += f"\n  📍 Entry: {aggregated.entry:.4f}"
            if aggregated.sl:
                message += f"\n  🛑 SL: {aggregated.sl:.4f}"
            if aggregated.tp:
                message += f"\n  🎯 TP: {aggregated.tp:.4f}"
        
        recommendation = self._generate_recommendation(aggregated)
        if recommendation:
            message += f"\n\n💡 <b>Note:</b> {recommendation}"

        message += f"\n\n⏰ <i>{aggregated.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        
        return message
    
    def _generate_recommendation(self, aggregated: AggregatedSignal) -> str:
        """Short non-advice copy for display only."""
        if aggregated.action == Action.BUY:
            if aggregated.confidence > 0.8:
                return "Strong confluence on the long side; confirm on your timeframe."
            if aggregated.confidence > 0.6:
                return "Moderate long bias; wait for additional confirmation."
            return "Weak long signal; avoid sizing up until structure improves."

        if aggregated.action == Action.SELL:
            if aggregated.confidence > 0.8:
                return "Strong pressure to the downside; reduce risk if positioned long."
            return "Moderate downside pressure."

        if aggregated.action == Action.EXIT:
            if aggregated.confidence > 0.8:
                return "High-risk cluster: consider de-risking immediately."
            return "Elevated risk; tighten risk controls."

        return "No clear edge; stand aside."

    async def _save_aggregated_signal(
        self, aggregated: AggregatedSignal, *, sent_telegram: bool
    ):
        """Persist aggregated decision to SQLite and optional outcome-tracking row."""
        try:
            baseline = aggregated.baseline_action or aggregated.action.value
            await self.db.save_signal(
                agent_type="aggregator",
                signal_type=aggregated.action.value.lower(),
                priority="high" if aggregated.confidence > 0.6 else "medium",
                message=f"{aggregated.action.value} signal for {aggregated.symbol}",
                symbol=aggregated.symbol,
                data={
                    "confidence": aggregated.confidence,
                    "risk": aggregated.risk.value,
                    "action": aggregated.action.value,
                    "baseline_action": baseline,
                    "reasons": aggregated.reasons,
                    "price": aggregated.price,
                    "entry": aggregated.entry,
                    "sl": aggregated.sl,
                    "tp": aggregated.tp,
                    "setup_quality": aggregated.setup_quality,
                    "setup_phase": aggregated.setup_phase,
                    "win_probability": aggregated.win_probability,
                    "source_signals_count": len(aggregated.source_signals),
                },
            )

            if getattr(config.agent, "outcome_tracking_enabled", True):
                horizon_sec = int(
                    float(getattr(config.agent, "outcome_horizon_hours", 4)) * 3600.0
                )
                council_changed = baseline != aggregated.action.value
                signal_ts = timegm(aggregated.timestamp.utctimetuple())
                await self.db.insert_aggregated_outcome(
                    signal_ts=signal_ts,
                    symbol=aggregated.symbol,
                    action=aggregated.action.value,
                    baseline_action=baseline,
                    confidence=float(aggregated.confidence),
                    risk=aggregated.risk.value,
                    price_at_signal=aggregated.price,
                    reasons=list(aggregated.reasons or []),
                    horizon_sec=horizon_sec,
                    council_enabled=getattr(
                        config.agent, "expert_council_enabled", True
                    ),
                    council_changed=council_changed,
                    sent_telegram=sent_telegram,
                    sl_price=aggregated.sl,
                    tp_price=aggregated.tp,
                )
        except Exception as e:
            self.logger.error("Failed to save aggregated signal: %s", e, exc_info=True)

    async def _send_periodic_reports(self):
        """Periodic analyst / activity summary to Telegram."""
        while self.running:
            try:
                analyst = bool(getattr(config.agent, "analyst_mode_enabled", True))
                hours = float(
                    getattr(config.agent, "analyst_report_interval_hours", 24.0)
                    if analyst
                    else 1.0
                )
                await asyncio.sleep(int(hours * 3600))

                if analyst:
                    from core.analyst_report import build_analyst_report

                    report = await build_analyst_report(self.db)
                else:
                    report = await self._generate_hourly_report()
                if report and self.telegram_bot:
                    await self.telegram_bot.send_daily_report(report)
            except Exception as e:
                self.logger.error("Periodic report failed: %s", e, exc_info=True)
                await asyncio.sleep(60)

    async def _generate_hourly_report(self) -> str:
        """HTML summary of the last hour."""
        try:
            hour_ago = datetime.utcnow() - timedelta(hours=1)
            
            symbols_with_signals = {}
            for symbol, signals in self.signals_by_symbol.items():
                recent = [s for s in signals if s.timestamp > hour_ago]
                if recent:
                    symbols_with_signals[symbol] = len(recent)
            
            if not symbols_with_signals:
                return None
            
            report = "📊 <b>Last hour summary</b>\n\n"
            report += f"Active symbols: {len(symbols_with_signals)}\n"
            report += f"Total signals: {sum(symbols_with_signals.values())}\n\n"

            top_symbols = sorted(
                symbols_with_signals.items(), key=lambda x: x[1], reverse=True
            )[:5]
            report += "<b>Most active symbols:</b>\n"
            for symbol, count in top_symbols:
                report += f"  • {symbol}: {count} signals\n"

            return report
        except Exception as e:
            self.logger.error("Hourly report build failed: %s", e, exc_info=True)
            return None

    async def stop(self):
        """Stop background tasks."""
        self.running = False

