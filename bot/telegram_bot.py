"""Telegram delivery for signals and periodic HTML reports."""
from telegram import Bot
from telegram.error import TelegramError

from core.event_router import Signal
from core.logger import get_logger


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.running = False
        self.logger = get_logger(__name__)

    async def send_signal(self, signal: Signal):
        try:
            message = self._format_signal_message(signal)
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            self.logger.info(
                "Signal sent: %s - %s", signal.agent_type, signal.signal_type
            )
        except TelegramError as e:
            self.logger.error("Telegram send failed: %s", e)
        except Exception as e:
            self.logger.error("Unexpected Telegram error: %s", e, exc_info=True)

    def _format_signal_message(self, signal: Signal) -> str:
        priority_emoji = {
            "critical": "🚨",
            "urgent": "⚡",
            "high": "🔥",
            "medium": "📊",
            "low": "ℹ️",
        }
        agent_emoji = {
            "market": "📈",
            "onchain": "🐋",
            "liquidity": "💧",
            "shitcoin": "💩",
            "emergency": "🚨",
        }
        emoji = priority_emoji.get(signal.priority.value, "📌")
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
                details.append(f"💰 <b>Price:</b> {signal.data['price']:.6f}")
            if "volume" in signal.data:
                details.append(f"📊 <b>Volume:</b> ${signal.data['volume']:,.0f}")
            if "change" in signal.data or "change_24h" in signal.data:
                change = signal.data.get("change") or signal.data.get("change_24h", 0)
                try:
                    change = float(change) if change is not None else 0
                    change_emoji = "📈" if change > 0 else "📉"
                    details.append(f"{change_emoji} <b>Change:</b> {change:.2f}%")
                except (ValueError, TypeError):
                    pass
            if "reason" in signal.data:
                details.append(f"💡 <b>Reason:</b> {signal.data['reason']}")
            if "action" in signal.data:
                action_emoji = "🟢" if signal.data["action"] == "BUY" else "🔴"
                details.append(f"{action_emoji} <b>Action:</b> {signal.data['action']}")
            if "risk" in signal.data:
                risk = signal.data["risk"]
                try:
                    risk = float(risk) if risk is not None else 0
                    risk_emoji = "🔴" if risk > 0.7 else "🟡" if risk > 0.4 else "🟢"
                    details.append(f"{risk_emoji} <b>Risk:</b> {risk:.1%}")
                except (ValueError, TypeError):
                    pass
            if "support" in signal.data:
                details.append(f"📉 <b>Support:</b> {signal.data['support']:.6f}")
            if "resistance" in signal.data:
                details.append(f"📈 <b>Resistance:</b> {signal.data['resistance']:.6f}")
            if "imbalance" in signal.data:
                imbalance = signal.data["imbalance"]
                try:
                    imbalance = float(imbalance) if imbalance is not None else 0
                    direction = "bids" if imbalance > 0 else "asks"
                    details.append(
                        f"⚖️ <b>Imbalance:</b> {abs(imbalance):.1%} ({direction})"
                    )
                except (ValueError, TypeError):
                    pass
            if details:
                message += "\n\n" + "\n".join(details)
        message += f"\n\n⏰ <i>{signal.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        return message

    async def send_daily_report(self, report_text: str):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"📊 <b>Report</b>\n\n{report_text}",
                parse_mode="HTML",
            )
        except Exception as e:
            self.logger.error("Report send failed: %s", e, exc_info=True)

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False
