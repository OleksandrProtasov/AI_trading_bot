"""Telegram delivery for signals and periodic HTML reports."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from telegram import Bot
from telegram.error import TelegramError

from bot.telegram_format import format_risk_alert, format_trade_alert
from core.event_router import Signal
from core.logger import get_logger


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.running = False
        self.logger = get_logger(__name__)

    async def send_trade_alert(
        self,
        *,
        symbol: str,
        action: str,
        entry: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        confidence: float = 0.0,
        risk: str = "Medium",
        reasons: Optional[List[str]] = None,
        chart_bytes: Optional[bytes] = None,
        setup_quality: int = 0,
        setup_phase: str = "ready",
        win_probability: float = 0.0,
        is_risk_alert: bool = False,
        setup_checklist: Optional[dict] = None,
        watch_zone_low: Optional[float] = None,
        watch_zone_high: Optional[float] = None,
        current_price: Optional[float] = None,
        entry_mode: str = "none",
        tp_liquidity_source: str = "",
    ) -> bool:
        """Send actionable trade or risk alert (optionally with chart)."""
        try:
            if is_risk_alert:
                caption = format_risk_alert(
                    symbol=symbol,
                    action=action,
                    confidence=confidence,
                    reasons=reasons or [],
                )
            else:
                caption = format_trade_alert(
                    symbol=symbol,
                    action=action,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    confidence=confidence,
                    risk=risk,
                    reasons=reasons or [],
                    setup_quality=setup_quality,
                    setup_phase=setup_phase,
                    win_probability=win_probability,
                    setup_checklist=setup_checklist,
                    watch_zone_low=watch_zone_low,
                    watch_zone_high=watch_zone_high,
                    current_price=current_price,
                    entry_mode=entry_mode,
                    tp_liquidity_source=tp_liquidity_source,
                )
            ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            caption = f"{caption}\n\n⏰ <i>{ts}</i>"

            if chart_bytes:
                await self.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=chart_bytes,
                    caption=caption,
                    parse_mode="HTML",
                )
            else:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            self.logger.info("Trade alert sent: %s %s", symbol, action)
            return True
        except TelegramError as e:
            self.logger.error("Telegram send failed: %s", e)
        except Exception as e:
            self.logger.error("Unexpected Telegram error: %s", e, exc_info=True)
        return False

    async def send_volume_spike_alert(
        self,
        *,
        symbol: str,
        ratio: float,
        price: float,
        price_change_pct: float,
        direction: str,
        setup_state: str = "",
        setup_side: str = "",
        zone_low: Optional[float] = None,
        zone_high: Optional[float] = None,
        quality: int = 0,
        trade_hint: str = "",
        candle_range_pct: float = 0.0,
    ) -> bool:
        from bot.telegram_format import format_volume_spike_alert

        try:
            caption = format_volume_spike_alert(
                symbol=symbol,
                ratio=ratio,
                price=price,
                price_change_pct=price_change_pct,
                direction=direction,
                setup_state=setup_state,
                setup_side=setup_side,
                zone_low=zone_low,
                zone_high=zone_high,
                quality=quality,
                trade_hint=trade_hint,
                candle_range_pct=candle_range_pct,
            )
            ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            caption = f"{caption}\n\n⏰ <i>{ts}</i>"
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=caption,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            self.logger.info("Volume spike alert sent: %s %.1fx", symbol, ratio)
            return True
        except TelegramError as e:
            self.logger.error("Telegram volume spike send failed: %s", e)
        except Exception as e:
            self.logger.error("Volume spike telegram error: %s", e, exc_info=True)
        return False

    async def send_signal(self, signal: Signal):
        """Legacy raw-signal path (kept for compatibility)."""
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
                text=f"🤖 <b>Trading Bot</b>\n\n{report_text}",
                parse_mode="HTML",
            )
        except Exception as e:
            self.logger.error("Report send failed: %s", e, exc_info=True)

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False
