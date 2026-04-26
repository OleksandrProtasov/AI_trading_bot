"""Aggregator agent: merges signals from all agents into weighted actions."""
import asyncio
from calendar import timegm
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from core.utils import is_stable_coin, validate_price
from core.metrics import Metrics
from core.expert_council import refine_aggregate
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

    def __repr__(self):
        return f"AggregatedSignal({self.symbol}, {self.action.value}, {self.confidence:.2%})"


class AggregatorAgent:
    def __init__(self, db: Database, event_router: EventRouter, telegram_bot=None):
        self.db = db
        self.event_router = event_router
        self.telegram_bot = telegram_bot
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
                "pump": 0.9,
                "dump": 1.0,
                "rapid_pump": 1.0,
                "rapid_dump": 1.0,
                "new_shitcoin": 0.3
            }
        }
        
        self.priority_weights = {
            Priority.CRITICAL: 1.0,
            Priority.URGENT: 0.9,
            Priority.HIGH: 0.7,
            Priority.MEDIUM: 0.4,
            Priority.LOW: 0.2
        }
        
        self.signal_queue = asyncio.Queue()

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
                if confidence < (min_conf + 0.05) or confirms < (req_confirms + 1):
                    return Action.WAIT, min(confidence, 0.40), reasons + [
                        "Defensive mode: waiting for stronger multi-agent confluence."
                    ]
            if action == Action.BUY and heavy_exit:
                return Action.WAIT, min(confidence, 0.35), reasons + [
                    "Defensive mode: elevated exit pressure detected."
                ]

        # balanced or unknown
        return action, confidence, reasons

    async def start(self):
        """Start background aggregation tasks."""
        self.running = True
        await asyncio.gather(
            self._collect_signals(),
            self._process_aggregation(),
            self._send_periodic_reports(),
        )

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
                        if self.telegram_bot:
                            await self._send_aggregated_signal(aggregated)
                            sent_telegram = True
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
            
            buy_score = self._calculate_score(buy_signals)
            sell_score = self._calculate_score(sell_signals)
            exit_score = self._calculate_score(exit_signals)

            max_score = max(buy_score, sell_score, exit_score)
            sorted_scores = sorted([buy_score, sell_score, exit_score], reverse=True)
            score_margin = sorted_scores[0] - sorted_scores[1]
            min_margin = 0.10

            if max_score < 0.3:
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
            weight = max(0.05, type_weight * (0.5 + 0.5 * priority_weight))
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
    
    async def _send_aggregated_signal(self, aggregated: AggregatedSignal):
        """Push formatted aggregated signal to Telegram."""
        try:
            message = self._format_aggregated_message(aggregated)
            await self.telegram_bot.send_signal(
                Signal(
                    agent_type="aggregator",
                    signal_type=aggregated.action.value.lower(),
                    priority=Priority.URGENT if aggregated.confidence > 0.8 else Priority.HIGH,
                    message=message,
                    symbol=aggregated.symbol,
                    data={
                        'confidence': aggregated.confidence,
                        'risk': aggregated.risk.value,
                        'action': aggregated.action.value,
                        'price': aggregated.price,
                        'entry': aggregated.entry,
                        'sl': aggregated.sl,
                        'tp': aggregated.tp
                    }
                )
            )
        except Exception as e:
            self.logger.error("Failed to send aggregated signal: %s", e, exc_info=True)

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
                )
        except Exception as e:
            self.logger.error("Failed to save aggregated signal: %s", e, exc_info=True)

    async def _send_periodic_reports(self):
        """Hourly summary to Telegram."""
        while self.running:
            try:
                await asyncio.sleep(3600)

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

