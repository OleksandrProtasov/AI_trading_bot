"""
aggregator_agent.py - центральный агент, объединяющий сигналы от всех агентов
"""
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
        self.source_signals = []  # Список исходных сигналов
    
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
        
        # Хранилище сигналов по символам
        self.signals_by_symbol = defaultdict(list)  # {symbol: [Signal]}
        
        # Дедупликация - храним последние отправленные сигналы
        self.last_sent_signals = {}  # {(symbol, action): timestamp}
        
        # Стабильные монеты (не должны генерировать сигналы)
        self.stable_coins = config.stable_coins
        
        # Веса для разных типов сигналов
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
        
        # Веса приоритетов
        self.priority_weights = {
            Priority.CRITICAL: 1.0,
            Priority.URGENT: 0.9,
            Priority.HIGH: 0.7,
            Priority.MEDIUM: 0.4,
            Priority.LOW: 0.2
        }
        
        # Подписка на сигналы от EventRouter
        self.signal_queue = asyncio.Queue()
    
    async def start(self):
        """Запуск агента"""
        self.running = True
        # Подписываемся на сигналы через модифицированный EventRouter
        await asyncio.gather(
            self._collect_signals(),
            self._process_aggregation(),
            self._send_periodic_reports()
        )
    
    async def _collect_signals(self):
        """Сбор сигналов от всех агентов"""
        # Сигналы приходят через add_signal() из EventRouter callback
        # Этот метод оставлен для будущих расширений
        while self.running:
            await asyncio.sleep(1)
    
    async def add_signal(self, signal: Signal):
        """Добавление сигнала от другого агента"""
        if signal.symbol:
            self.signals_by_symbol[signal.symbol].append(signal)
            # Храним только последние 50 сигналов на символ
            if len(self.signals_by_symbol[signal.symbol]) > 50:
                self.signals_by_symbol[signal.symbol] = self.signals_by_symbol[signal.symbol][-50:]
    
    async def _process_aggregation(self):
        """Обработка и агрегация сигналов"""
        while self.running:
            try:
                await asyncio.sleep(config.agent.aggregation_interval)
                
                for symbol, signals in list(self.signals_by_symbol.items()):
                    if not signals:
                        continue
                    
                    # Фильтруем только свежие сигналы
                    recent_signals = [
                        s for s in signals 
                        if (datetime.utcnow() - s.timestamp).total_seconds() < config.agent.recent_signals_window
                    ]
                    
                    if not recent_signals:
                        continue
                    
                    # Пропускаем стабильные монеты
                    if is_stable_coin(symbol, self.stable_coins):
                        self.logger.debug(f"Пропущен стабильный токен: {symbol}")
                        continue
                    
                    # Пропускаем если символ слишком короткий (не валидная пара)
                    if len(symbol) < 6:
                        continue
                    
                    # Агрегируем сигналы
                    aggregated = await self._aggregate_signals(symbol, recent_signals)
                    
                    if aggregated and aggregated.confidence >= config.agent.min_confidence:
                        # Проверяем валидность цены
                        if aggregated.price is not None and aggregated.price <= 0:
                            self.logger.debug(f"Пропущен сигнал для {symbol}: невалидная цена {aggregated.price}")
                            continue
                        
                        # Дедупликация
                        signal_key = (symbol, aggregated.action.value)
                        last_sent = self.last_sent_signals.get(signal_key, 0)
                        if last_sent:
                            from datetime import datetime as dt
                            last_sent_dt = dt.fromtimestamp(last_sent)
                            time_since_last = (datetime.utcnow() - last_sent_dt).total_seconds()
                        else:
                            time_since_last = 999
                        
                        if time_since_last < config.agent.signal_deduplication_window:
                                # Пропускаем - уже отправляли недавно
                            self.logger.debug(f"Пропущен дубликат сигнала: {symbol} {aggregated.action.value}")
                            continue
                        
                        # Отправляем в Telegram
                        if self.telegram_bot:
                            await self._send_aggregated_signal(aggregated)
                            self.last_sent_signals[signal_key] = datetime.utcnow().timestamp()
                            self.metrics.record_signal("aggregator", aggregated.action.value.lower(), symbol)
                        
                        # Сохраняем в БД
                        await self._save_aggregated_signal(aggregated)
                        
            except Exception as e:
                self.logger.error(f"Ошибка обработки: {e}", exc_info=True)
                self.metrics.record_error()
                await asyncio.sleep(5)
    
    async def _aggregate_signals(self, symbol: str, signals: List[Signal]) -> Optional[AggregatedSignal]:
        """Агрегация сигналов в финальное решение"""
        try:
            # Группируем сигналы по типу действия
            buy_signals = []
            sell_signals = []
            exit_signals = []
            
            for signal in signals:
                signal_type = signal.signal_type.lower()
                agent_type = signal.agent_type.lower()
                
                # Определяем направление сигнала
                if any(x in signal_type for x in ['pump', 'break', 'buy', 'whale_activity', 'imbalance']):
                    if 'dump' not in signal_type and 'exit' not in signal_type:
                        buy_signals.append(signal)
                
                if any(x in signal_type for x in ['dump', 'sell', 'exit', 'danger', 'crisis']):
                    exit_signals.append(signal)
                
                if 'sell' in signal_type or ('dump' in signal_type and 'rapid' in signal_type):
                    sell_signals.append(signal)
            
            # Вычисляем скоринг для каждого действия
            buy_score = self._calculate_score(buy_signals)
            sell_score = self._calculate_score(sell_signals)
            exit_score = self._calculate_score(exit_signals)
            
            # Определяем финальное действие
            max_score = max(buy_score, sell_score, exit_score)
            
            if max_score < 0.3:  # Слишком низкий скоринг
                return None
            
            # Определяем действие
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
            
            # Определяем уровень риска
            risk = self._calculate_risk(signals)
            
            # Получаем цену из сигналов
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
            
            return aggregated
            
        except Exception as e:
            self.logger.error(f"Ошибка агрегации для {symbol}: {e}", exc_info=True)
            return None
    
    def _calculate_score(self, signals: List[Signal]) -> float:
        """Вычисление скоринга для группы сигналов"""
        if not signals:
            return 0.0
        
        total_score = 0.0
        total_weight = 0.0
        
        for signal in signals:
            agent_type = signal.agent_type.lower()
            signal_type = signal.signal_type.lower()
            
            # Вес типа сигнала
            type_weight = self.signal_weights.get(agent_type, {}).get(signal_type, 0.5)
            
            # Вес приоритета
            priority_weight = self.priority_weights.get(signal.priority, 0.5)
            
            # Итоговый вес
            weight = type_weight * priority_weight
            total_weight += weight
            total_score += weight
        
        # Нормализуем (максимум 1.0)
        if total_weight > 0:
            score = min(total_score / total_weight, 1.0)
        else:
            score = 0.0
        
        # Бонус за количество сигналов (консенсус)
        if len(signals) > 1:
            consensus_bonus = min(len(signals) * 0.05, 0.2)
            score = min(score + consensus_bonus, 1.0)
        
        return score
    
    def _extract_reasons(self, signals: List[Signal]) -> List[str]:
        """Извлечение причин из сигналов"""
        reasons = []
        seen_reasons = set()
        
        for signal in signals:
            # Извлекаем причину из сообщения или data
            reason = None
            
            if signal.data and 'reason' in signal.data:
                reason = signal.data['reason']
            else:
                # Парсим из сообщения
                msg_lower = signal.message.lower()
                if 'пробой' in msg_lower or 'break' in msg_lower:
                    reason = f"Пробой уровня ({signal.signal_type})"
                elif 'объем' in msg_lower or 'volume' in msg_lower:
                    if signal.data and 'volume_spike' in signal.data:
                        spike = signal.data.get('volume_spike', 0)
                        reason = f"Всплеск объема +{spike:.1f}x"
                    else:
                        reason = "Всплеск объема"
                elif 'кит' in msg_lower or 'whale' in msg_lower:
                    if signal.data and 'volume_usd' in signal.data:
                        volume = signal.data['volume_usd']
                        reason = f"Кит активность ${volume:,.0f}"
                    else:
                        reason = "Активность китов"
                elif 'имбаланс' in msg_lower or 'imbalance' in msg_lower:
                    if signal.data and 'imbalance' in signal.data:
                        imb = signal.data['imbalance']
                        direction = "покупка" if imb > 0 else "продажа"
                        reason = f"Имбаланс стакана {abs(imb):.1%} ({direction})"
                    else:
                        reason = "Имбаланс стакана"
                elif 'дамп' in msg_lower or 'dump' in msg_lower:
                    reason = "Опасность дампа"
                elif 'памп' in msg_lower or 'pump' in msg_lower:
                    reason = "Потенциал пампа"
                else:
                    reason = f"{signal.agent_type}: {signal.signal_type}"
            
            if reason and reason not in seen_reasons:
                reasons.append(reason)
                seen_reasons.add(reason)
        
        return reasons[:5]  # Максимум 5 причин
    
    def _calculate_risk(self, signals: List[Signal]) -> RiskLevel:
        """Вычисление уровня риска"""
        risk_score = 0.0
        
        for signal in signals:
            # Критические и срочные сигналы = высокий риск
            if signal.priority in [Priority.CRITICAL, Priority.URGENT]:
                risk_score += 0.5
            elif signal.priority == Priority.HIGH:
                risk_score += 0.3
            
            # Emergency сигналы = высокий риск
            if signal.agent_type == "emergency":
                risk_score += 0.4
            
            # Shitcoin сигналы = высокий риск
            if signal.agent_type == "shitcoin":
                if signal.data and 'risk' in signal.data:
                    risk_score += signal.data['risk']
                else:
                    risk_score += 0.5
        
        # Нормализуем
        risk_score = min(risk_score, 1.0)
        
        if risk_score > 0.7:
            return RiskLevel.HIGH
        elif risk_score > 0.4:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    async def _send_aggregated_signal(self, aggregated: AggregatedSignal):
        """Отправка агрегированного сигнала в Telegram"""
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
            self.logger.error(f"Ошибка отправки сигнала: {e}", exc_info=True)
    
    def _format_aggregated_message(self, aggregated: AggregatedSignal) -> str:
        """Форматирование сообщения для Telegram"""
        # Эмодзи для действий
        action_emoji = {
            Action.BUY: "🟢",
            Action.SELL: "🔴",
            Action.EXIT: "⚠️",
            Action.WAIT: "⏸️"
        }
        
        # Эмодзи для риска
        risk_emoji = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🔴"
        }
        
        emoji = action_emoji.get(aggregated.action, "📊")
        risk_icon = risk_emoji.get(aggregated.risk, "⚪")
        
        # Заголовок
        priority_text = "URGENT" if aggregated.confidence > 0.8 else "HIGH" if aggregated.confidence > 0.6 else "MEDIUM"
        header = f"{emoji} <b>{priority_text} {aggregated.action.value}</b>"
        if aggregated.symbol:
            header += f"\n{aggregated.symbol}"
            if aggregated.price:
                header += f" @ {aggregated.price:.4f}"
        
        message = header
        
        # Уверенность и риск
        message += f"\n\n📊 <b>Уверенность:</b> {aggregated.confidence:.1%}"
        message += f"\n{risk_icon} <b>Риск:</b> {aggregated.risk.value}"
        
        # Причины
        if aggregated.reasons:
            message += "\n\n<b>Причины:</b>"
            for reason in aggregated.reasons:
                message += f"\n  • {reason}"
        
        # Уровни входа/выхода
        if aggregated.entry or aggregated.sl or aggregated.tp:
            message += "\n\n<b>Уровни:</b>"
            if aggregated.entry:
                message += f"\n  📍 Entry: {aggregated.entry:.4f}"
            if aggregated.sl:
                message += f"\n  🛑 SL: {aggregated.sl:.4f}"
            if aggregated.tp:
                message += f"\n  🎯 TP: {aggregated.tp:.4f}"
        
        # Рекомендация
        recommendation = self._generate_recommendation(aggregated)
        if recommendation:
            message += f"\n\n💡 <b>Рекомендация:</b> {recommendation}"
        
        # Время
        message += f"\n\n⏰ <i>{aggregated.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        
        return message
    
    def _generate_recommendation(self, aggregated: AggregatedSignal) -> str:
        """Генерация текстовой рекомендации"""
        if aggregated.action == Action.BUY:
            if aggregated.confidence > 0.8:
                return "Вход подтверждён, тренд ускоряется."
            elif aggregated.confidence > 0.6:
                return "Умеренный сигнал на вход, следите за подтверждением."
            else:
                return "Слабый сигнал, рекомендуется подождать подтверждения."
        
        elif aggregated.action == Action.SELL:
            if aggregated.confidence > 0.8:
                return "Сильный сигнал на продажу, рекомендуется выход."
            else:
                return "Умеренный сигнал на продажу."
        
        elif aggregated.action == Action.EXIT:
            if aggregated.confidence > 0.8:
                return "КРИТИЧЕСКИЙ СИГНАЛ: Немедленный выход!"
            else:
                return "Рекомендуется выход из позиции."
        
        else:
            return "Ожидание более четких сигналов."
    
    async def _save_aggregated_signal(self, aggregated: AggregatedSignal):
        """Сохранение агрегированного сигнала в БД"""
        try:
            await self.db.save_signal(
                agent_type="aggregator",
                signal_type=aggregated.action.value.lower(),
                priority="high" if aggregated.confidence > 0.6 else "medium",
                message=f"{aggregated.action.value} сигнал для {aggregated.symbol}",
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
            self.logger.error(f"Ошибка сохранения: {e}", exc_info=True)
    
    async def _send_periodic_reports(self):
        """Отправка периодических отчетов"""
        while self.running:
            try:
                await asyncio.sleep(3600)  # Каждый час
                
                # Формируем сводку за последний час
                report = await self._generate_hourly_report()
                if report and self.telegram_bot:
                    await self.telegram_bot.send_daily_report(report)
            except Exception as e:
                self.logger.error(f"Ошибка отправки отчета: {e}", exc_info=True)
                await asyncio.sleep(60)
    
    async def _generate_hourly_report(self) -> str:
        """Генерация часового отчета"""
        try:
            # Подсчитываем статистику за последний час
            hour_ago = datetime.utcnow() - timedelta(hours=1)
            
            symbols_with_signals = {}
            for symbol, signals in self.signals_by_symbol.items():
                recent = [s for s in signals if s.timestamp > hour_ago]
                if recent:
                    symbols_with_signals[symbol] = len(recent)
            
            if not symbols_with_signals:
                return None
            
            report = "📊 <b>Сводка за последний час</b>\n\n"
            report += f"Активных символов: {len(symbols_with_signals)}\n"
            report += f"Всего сигналов: {sum(symbols_with_signals.values())}\n\n"
            
            # Топ символов по активности
            top_symbols = sorted(symbols_with_signals.items(), key=lambda x: x[1], reverse=True)[:5]
            report += "<b>Топ активных символов:</b>\n"
            for symbol, count in top_symbols:
                report += f"  • {symbol}: {count} сигналов\n"
            
            return report
        except Exception as e:
            self.logger.error(f"Ошибка генерации отчета: {e}", exc_info=True)
            return None
    
    async def stop(self):
        """Остановка агента"""
        self.running = False

