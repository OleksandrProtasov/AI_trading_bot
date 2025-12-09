"""
event_router.py - объединяет сигналы от всех агентов и фильтрует их
"""
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum
from core.database import Database
from core.logger import get_logger


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class Signal:
    def __init__(self, agent_type: str, signal_type: str, priority: Priority,
                 message: str, symbol: Optional[str] = None, data: Optional[Dict] = None):
        self.agent_type = agent_type
        self.signal_type = signal_type
        self.priority = priority
        self.message = message
        self.symbol = symbol
        self.data = data or {}
        self.timestamp = datetime.utcnow()
        self.id = None  # будет установлен после сохранения в БД
    
    def __repr__(self):
        return f"Signal({self.agent_type}, {self.signal_type}, {self.priority.value}, {self.symbol})"


class EventRouter:
    def __init__(self, db: Database, telegram_handler=None, aggregator_callback=None):
        self.db = db
        self.telegram_handler = telegram_handler
        self.aggregator_callback = aggregator_callback  # Callback для AggregatorAgent
        self.signal_queue = asyncio.Queue()
        self.running = False
        self.logger = get_logger(__name__)
        self.priority_weights = {
            Priority.CRITICAL: 5,
            Priority.URGENT: 4,
            Priority.HIGH: 3,
            Priority.MEDIUM: 2,
            Priority.LOW: 1
        }
    
    async def add_signal(self, signal: Signal):
        """Добавить сигнал в очередь"""
        await self.signal_queue.put(signal)
    
    async def process_signals(self):
        """Обработка сигналов из очереди"""
        self.running = True
        while self.running:
            try:
                # Получаем сигнал с таймаутом
                signal = await asyncio.wait_for(self.signal_queue.get(), timeout=1.0)
                
                # Сохраняем в БД
                signal_id = await self.db.save_signal(
                    agent_type=signal.agent_type,
                    signal_type=signal.signal_type,
                    priority=signal.priority.value,
                    message=signal.message,
                    symbol=signal.symbol,
                    data=signal.data
                )
                signal.id = signal_id
                
                # Отправляем в AggregatorAgent для агрегации
                if self.aggregator_callback:
                    try:
                        await self.aggregator_callback(signal)
                    except Exception as e:
                        self.logger.error(f"Ошибка отправки в aggregator: {e}", exc_info=True)
                
                # Отправляем в Telegram если нужно (старая логика - теперь AggregatorAgent решает)
                # Оставляем для обратной совместимости, но можно отключить
                if self.telegram_handler:
                    should_send = await self._should_send_to_telegram(signal)
                    if should_send:
                        await self.telegram_handler.send_signal(signal)
                        if signal_id:
                            await self.db.mark_signal_sent(signal_id)
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error(f"Ошибка обработки сигнала: {e}", exc_info=True)
    
    async def _should_send_to_telegram(self, signal: Signal) -> bool:
        """Определяет, нужно ли отправлять сигнал в Telegram"""
        # Критические и срочные сигналы всегда отправляем
        if signal.priority in [Priority.CRITICAL, Priority.URGENT]:
            return True
        
        # EmergencyAgent сигналы всегда отправляем
        if signal.agent_type == "emergency":
            return True
        
        # Высокоприоритетные сигналы отправляем
        if signal.priority == Priority.HIGH:
            return True
        
        # Для остальных - фильтруем по типу
        important_types = ["buy", "sell", "exit", "whale_alert", "liquidity_break"]
        if signal.signal_type in important_types:
            return True
        
        return False
    
    def format_signal_message(self, signal: Signal) -> str:
        """Форматирование сообщения для Telegram"""
        priority_emoji = {
            Priority.CRITICAL: "🚨",
            Priority.URGENT: "⚡",
            Priority.HIGH: "🔥",
            Priority.MEDIUM: "📊",
            Priority.LOW: "ℹ️"
        }
        
        agent_emoji = {
            "market": "📈",
            "onchain": "🐋",
            "liquidity": "💧",
            "shitcoin": "💩",
            "emergency": "🚨"
        }
        
        emoji = priority_emoji.get(signal.priority, "📌")
        agent_icon = agent_emoji.get(signal.agent_type, "🤖")
        
        header = f"{emoji} {agent_icon} <b>{signal.agent_type.upper()}</b> - {signal.signal_type.upper()}"
        if signal.symbol:
            header += f" | {signal.symbol}"
        
        message = f"{header}\n\n{signal.message}"
        
        # Добавляем дополнительные данные если есть
        if signal.data:
            details = []
            if "price" in signal.data:
                details.append(f"💰 Цена: {signal.data['price']}")
            if "volume" in signal.data:
                details.append(f"📊 Объем: {signal.data['volume']}")
            if "change" in signal.data:
                details.append(f"📈 Изменение: {signal.data['change']}%")
            if "reason" in signal.data:
                details.append(f"💡 Причина: {signal.data['reason']}")
            
            if details:
                message += "\n\n" + "\n".join(details)
        
        return message
    
    async def stop(self):
        """Остановка роутера"""
        self.running = False

