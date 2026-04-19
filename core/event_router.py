"""Route signals from agents to storage, optional Telegram, and the aggregator."""
import asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from core.database import Database
from core.logger import get_logger


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"


class Signal:
    def __init__(
        self,
        agent_type: str,
        signal_type: str,
        priority: Priority,
        message: str,
        symbol: Optional[str] = None,
        data: Optional[Dict] = None,
    ):
        self.agent_type = agent_type
        self.signal_type = signal_type
        self.priority = priority
        self.message = message
        self.symbol = symbol
        self.data = data or {}
        self.timestamp = datetime.utcnow()
        self.id = None

    def __repr__(self):
        return (
            f"Signal({self.agent_type}, {self.signal_type}, "
            f"{self.priority.value}, {self.symbol})"
        )


class EventRouter:
    def __init__(self, db: Database, telegram_handler=None, aggregator_callback=None):
        self.db = db
        self.telegram_handler = telegram_handler
        self.aggregator_callback = aggregator_callback
        self.signal_queue = asyncio.Queue()
        self.running = False
        self.logger = get_logger(__name__)
        self.priority_weights = {
            Priority.CRITICAL: 5,
            Priority.URGENT: 4,
            Priority.HIGH: 3,
            Priority.MEDIUM: 2,
            Priority.LOW: 1,
        }

    async def add_signal(self, signal: Signal):
        """Enqueue a signal for persistence and downstream consumers."""
        await self.signal_queue.put(signal)

    async def process_signals(self):
        """Drain the queue: save to DB, notify aggregator, optional Telegram."""
        self.running = True
        while self.running:
            try:
                signal = await asyncio.wait_for(self.signal_queue.get(), timeout=1.0)

                signal_id = await self.db.save_signal(
                    agent_type=signal.agent_type,
                    signal_type=signal.signal_type,
                    priority=signal.priority.value,
                    message=signal.message,
                    symbol=signal.symbol,
                    data=signal.data,
                )
                signal.id = signal_id

                if self.aggregator_callback:
                    try:
                        await self.aggregator_callback(signal)
                    except Exception as e:
                        self.logger.error(
                            "Aggregator callback failed: %s", e, exc_info=True
                        )

                if self.telegram_handler:
                    should_send = await self._should_send_to_telegram(signal)
                    if should_send:
                        await self.telegram_handler.send_signal(signal)
                        if signal_id:
                            await self.db.mark_signal_sent(signal_id)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error("Signal processing error: %s", e, exc_info=True)

    async def _should_send_to_telegram(self, signal: Signal) -> bool:
        """Legacy direct-to-Telegram path (aggregator usually owns alerts)."""
        if signal.priority in [Priority.CRITICAL, Priority.URGENT]:
            return True
        if signal.agent_type == "emergency":
            return True
        if signal.priority == Priority.HIGH:
            return True
        important_types = ["buy", "sell", "exit", "whale_alert", "liquidity_break"]
        if signal.signal_type in important_types:
            return True
        return False

    def format_signal_message(self, signal: Signal) -> str:
        """HTML formatting helper (used by legacy paths)."""
        priority_emoji = {
            Priority.CRITICAL: "🚨",
            Priority.URGENT: "⚡",
            Priority.HIGH: "🔥",
            Priority.MEDIUM: "📊",
            Priority.LOW: "ℹ️",
        }
        agent_emoji = {
            "market": "📈",
            "onchain": "🐋",
            "liquidity": "💧",
            "shitcoin": "💩",
            "emergency": "🚨",
        }
        emoji = priority_emoji.get(signal.priority, "📌")
        agent_icon = agent_emoji.get(signal.agent_type, "🤖")
        header = (
            f"{emoji} {agent_icon} <b>{signal.agent_type.upper()}</b> - "
            f"{signal.signal_type.upper()}"
        )
        if signal.symbol:
            header += f" | {signal.symbol}"
        message = f"{header}\n\n{signal.message}"
        if signal.data:
            details = []
            if "price" in signal.data:
                details.append(f"💰 Price: {signal.data['price']}")
            if "volume" in signal.data:
                details.append(f"📊 Volume: {signal.data['volume']}")
            if "change" in signal.data:
                details.append(f"📈 Change: {signal.data['change']}%")
            if "reason" in signal.data:
                details.append(f"💡 Reason: {signal.data['reason']}")
            if details:
                message += "\n\n" + "\n".join(details)
        return message

    async def stop(self):
        self.running = False
