"""Route signals from agents to storage, optional Telegram, and the aggregator."""
import asyncio
from datetime import datetime
from enum import Enum
import time
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
    def __init__(
        self,
        db: Database,
        telegram_handler=None,
        aggregator_callback=None,
        *,
        forward_all_raw_to_telegram: bool = True,
    ):
        self.db = db
        self.telegram_handler = telegram_handler
        self.aggregator_callback = aggregator_callback
        self.forward_all_raw_to_telegram = forward_all_raw_to_telegram
        self.signal_queue = asyncio.Queue()
        self.running = False
        self.logger = get_logger(__name__)
        # Telegram anti-flood (keeps chat readable and avoids API rate limits).
        self.telegram_global_min_interval_sec = 2.0
        self.telegram_same_signal_cooldown_sec = 120.0
        self._telegram_last_global_sent_at = 0.0
        self._telegram_last_by_key: Dict[str, float] = {}
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
                    if should_send and self._can_send_telegram(signal):
                        await self.telegram_handler.send_signal(signal)
                        if signal_id:
                            await self.db.mark_signal_sent(signal_id)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error("Signal processing error: %s", e, exc_info=True)

    async def _should_send_to_telegram(self, signal: Signal) -> bool:
        """Telegram path for per-agent raw signals (aggregator sends separately)."""
        if signal.agent_type == "aggregator":
            return False
        if self.forward_all_raw_to_telegram:
            return True
        # strict mode: only the most actionable/high-risk events
        if signal.priority in [Priority.CRITICAL, Priority.URGENT]:
            return True
        important_types = ["buy", "sell", "exit", "whale_alert", "liquidity_break"]
        if signal.signal_type in important_types:
            return True
        return False

    def _can_send_telegram(self, signal: Signal) -> bool:
        """
        Apply anti-flood gates:
        - global minimum interval between telegram messages
        - cooldown for same (agent, type, symbol)
        """
        now = time.time()
        if now - self._telegram_last_global_sent_at < self.telegram_global_min_interval_sec:
            return False

        key = f"{signal.agent_type}:{signal.signal_type}:{signal.symbol or '-'}"
        last = self._telegram_last_by_key.get(key, 0.0)
        if now - last < self.telegram_same_signal_cooldown_sec:
            return False

        self._telegram_last_global_sent_at = now
        self._telegram_last_by_key[key] = now
        return True

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
