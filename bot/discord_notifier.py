"""
discord_notifier.py - уведомления в Discord через webhook
"""
import aiohttp
from typing import Optional
from core.logger import get_logger
from core.event_router import Signal
from agents.aggregator_agent import AggregatedSignal


class DiscordNotifier:
    """Отправка уведомлений в Discord"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url
        self.logger = get_logger(__name__)
        self.enabled = webhook_url is not None
    
    async def send_signal(self, signal: Signal):
        """Отправка сигнала в Discord"""
        if not self.enabled:
            return
        
        try:
            # Определяем цвет по приоритету
            color_map = {
                'critical': 0xff0000,  # Красный
                'urgent': 0xff8800,     # Оранжевый
                'high': 0xffaa00,      # Желтый
                'medium': 0x00aa00,    # Зеленый
                'low': 0x888888        # Серый
            }
            color = color_map.get(signal.priority.value.lower(), 0x888888)
            
            # Эмодзи по типу агента
            emoji_map = {
                'market': '📈',
                'onchain': '🐋',
                'liquidity': '💧',
                'shitcoin': '💩',
                'emergency': '🚨',
                'aggregator': '🤖'
            }
            emoji = emoji_map.get(signal.agent_type.lower(), '📊')
            
            embed = {
                "title": f"{emoji} {signal.signal_type.upper()}",
                "description": signal.message[:2000],  # Discord лимит
                "color": color,
                "fields": [
                    {"name": "Символ", "value": signal.symbol or "N/A", "inline": True},
                    {"name": "Приоритет", "value": signal.priority.value.upper(), "inline": True},
                    {"name": "Агент", "value": signal.agent_type, "inline": True}
                ],
                "timestamp": signal.timestamp.isoformat()
            }
            
            payload = {"embeds": [embed]}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status == 204:
                        self.logger.debug(f"Сигнал отправлен в Discord: {signal.signal_type}")
                    else:
                        self.logger.warning(f"Discord webhook вернул статус {resp.status}")
        except Exception as e:
            self.logger.error(f"Ошибка отправки в Discord: {e}", exc_info=True)
    
    async def send_aggregated_signal(self, aggregated: AggregatedSignal):
        """Отправка агрегированного сигнала"""
        if not self.enabled:
            return
        
        try:
            # Цвет по действию
            color_map = {
                'BUY': 0x00ff00,   # Зеленый
                'SELL': 0xff0000,  # Красный
                'EXIT': 0xff8800,  # Оранжевый
                'WAIT': 0x888888   # Серый
            }
            color = color_map.get(aggregated.action.value, 0x888888)
            
            # Эмодзи по действию
            emoji_map = {
                'BUY': '🟢',
                'SELL': '🔴',
                'EXIT': '⚠️',
                'WAIT': '⏸️'
            }
            emoji = emoji_map.get(aggregated.action.value, '📊')
            
            # Формируем причины
            reasons_text = "\n".join([f"• {r}" for r in aggregated.reasons[:5]])
            
            embed = {
                "title": f"{emoji} {aggregated.action.value} | {aggregated.symbol}",
                "description": f"**Уверенность:** {aggregated.confidence*100:.1f}%\n"
                              f"**Риск:** {aggregated.risk.value}\n\n"
                              f"**Причины:**\n{reasons_text}",
                "color": color,
                "fields": []
            }
            
            if aggregated.price:
                embed["fields"].append({
                    "name": "💰 Цена",
                    "value": f"{aggregated.price:.4f}",
                    "inline": True
                })
            
            if aggregated.entry:
                embed["fields"].append({
                    "name": "📍 Entry",
                    "value": f"{aggregated.entry:.4f}",
                    "inline": True
                })
            
            if aggregated.sl:
                embed["fields"].append({
                    "name": "🛑 SL",
                    "value": f"{aggregated.sl:.4f}",
                    "inline": True
                })
            
            if aggregated.tp:
                embed["fields"].append({
                    "name": "🎯 TP",
                    "value": f"{aggregated.tp:.4f}",
                    "inline": True
                })
            
            embed["timestamp"] = aggregated.timestamp.isoformat()
            
            payload = {"embeds": [embed]}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status == 204:
                        self.logger.debug(f"Агрегированный сигнал отправлен в Discord: {aggregated.symbol} {aggregated.action.value}")
                    else:
                        self.logger.warning(f"Discord webhook вернул статус {resp.status}")
        except Exception as e:
            self.logger.error(f"Ошибка отправки агрегированного сигнала в Discord: {e}", exc_info=True)




