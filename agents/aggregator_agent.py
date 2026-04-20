"""Aggregator agent: merges signals from all agents into weighted actions."""
import asyncio
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

                        if self.telegram_bot:
                            await self._send_aggregated_signal(aggregated)
                            self.last_sent_signals[signal_key] = datetime.utcnow().timestamp()
                            self.metrics.record_signal(
                                "aggregator",
                                aggregated.action.value.lower(),
                                symbol,
                            )

                        await self._save_aggregated_signal(aggregated)

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
                signal_type = signal.signal_type.lower()
                if any(x in signal_type for x in ['pump', 'break', 'buy', 'whale_activity', 'imbalance']):
                    if 'dump' not in signal_type and 'exit' not in signal_type:
                        buy_signals.append(signal)
                
                if any(x in signal_type for x in ['dump', 'sell', 'exit', 'danger', 'crisis']):
                    exit_signals.append(signal)
                
                if 'sell' in signal_type or ('dump' in signal_type and 'rapid' in signal_type):
                    sell_signals.append(signal)
            
            buy_score = self._calculate_score(buy_signals)
            sell_score = self._calculate_score(sell_signals)
            exit_score = self._calculate_score(exit_signals)

            max_score = max(buy_score, sell_score, exit_score)

            if max_score < 0.3:
                return None
            if exit_score >= max_score * 0.9:
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

            if getattr(config.agent, "expert_council_enabled", True):
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

            return aggregated
            
        except Exception as e:
            self.logger.error("Aggregation failed for %s: %s", symbol, e, exc_info=True)
            return None
    
    def _calculate_score(self, signals: List[Signal]) -> float:
        """Weighted score for a group of signals."""
        if not signals:
            return 0.0
        
        total_score = 0.0
        total_weight = 0.0
        
        for signal in signals:
            agent_type = signal.agent_type.lower()
            signal_type = signal.signal_type.lower()
            
            type_weight = self.signal_weights.get(agent_type, {}).get(signal_type, 0.5)
            priority_weight = self.priority_weights.get(signal.priority, 0.5)
            weight = type_weight * priority_weight
            total_weight += weight
            total_score += weight
        
        if total_weight > 0:
            score = min(total_score / total_weight, 1.0)
        else:
            score = 0.0
        
        if len(signals) > 1:
            consensus_bonus = min(len(signals) * 0.05, 0.2)
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

    async def _save_aggregated_signal(self, aggregated: AggregatedSignal):
        """Persist aggregated decision to SQLite."""
        try:
            await self.db.save_signal(
                agent_type="aggregator",
                signal_type=aggregated.action.value.lower(),
                priority="high" if aggregated.confidence > 0.6 else "medium",
                message=f"{aggregated.action.value} signal for {aggregated.symbol}",
                symbol=aggregated.symbol,
                data={
                    'confidence': aggregated.confidence,
                    'risk': aggregated.risk.value,
                    'action': aggregated.action.value,
                    'reasons': aggregated.reasons,
                    'price': aggregated.price,
                    'entry': aggregated.entry,
                    'sl': aggregated.sl,
                    'tp': aggregated.tp,
                    'source_signals_count': len(aggregated.source_signals)
                }
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

