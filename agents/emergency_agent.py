"""
emergency_agent.py - срочные сигналы в реальном времени (вход/выход)
"""
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from core.utils import is_stable_coin, validate_price
from config import config


class EmergencyAgent:
    def __init__(self, db: Database, event_router: EventRouter, market_agent, liquidity_agent):
        self.db = db
        self.event_router = event_router
        self.market_agent = market_agent
        self.liquidity_agent = liquidity_agent
        self.running = False
        self.last_prices = {}  # {symbol: price}
        self.logger = get_logger(__name__)
        self.volume_threshold = config.agent.volume_spike_threshold
        self.price_change_threshold = config.agent.price_change_threshold
        self.stable_coins = config.stable_coins
    
    async def start(self):
        """Запуск агента"""
        self.running = True
        await self._monitor_emergency_events()
    
    async def _monitor_emergency_events(self):
        """Мониторинг срочных событий"""
        while self.running:
            try:
                await asyncio.sleep(config.agent.emergency_check_interval)
                
                # Анализируем каждую отслеживаемую пару
                for symbol in self.market_agent.symbols:
                    await self._check_emergency_conditions(symbol)
                    
            except Exception as e:
                self.logger.error(f"Ошибка мониторинга: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    async def _check_emergency_conditions(self, symbol: str):
        """Проверка срочных условий для символа"""
        try:
            # Пропускаем стабильные монеты
            if is_stable_coin(symbol, self.stable_coins):
                return
            
            # Получаем последние данные
            candles = self.market_agent.candle_data.get(symbol, [])
            if len(candles) < 10:
                return
            
            current_candle = candles[-1]
            current_price = current_candle['close']
            current_volume = current_candle['volume']
            
            # Вычисляем средний объем
            avg_volume = sum([c['volume'] for c in candles[-10:]]) / min(len(candles), 10)
            
            # Проверка всплеска объема
            if avg_volume > 0 and current_volume > avg_volume * self.volume_threshold:
                await self._trigger_volume_spike_alert(symbol, current_price, current_volume, avg_volume)
            
            # Проверка резкого изменения цены
            if symbol in self.last_prices:
                price_change = abs(current_price - self.last_prices[symbol]) / self.last_prices[symbol]
                if price_change > self.price_change_threshold:
                    direction = "UP" if current_price > self.last_prices[symbol] else "DOWN"
                    await self._trigger_price_spike_alert(symbol, current_price, price_change, direction)
            
            self.last_prices[symbol] = current_price
            
            # Проверка ликвидности
            orderbook = self.market_agent.order_books.get(symbol)
            if orderbook:
                await self._check_liquidity_crisis(symbol, orderbook, current_price)
            
            # Проверка опасности дампа
            await self._check_dump_danger(symbol, candles, current_price)
            
        except Exception as e:
            self.logger.error(f"Ошибка проверки условий для {symbol}: {e}", exc_info=True)
    
    async def _trigger_volume_spike_alert(self, symbol: str, price: float, volume: float, avg_volume: float):
        """Алерт о всплеске объема"""
        spike_ratio = volume / avg_volume if avg_volume > 0 else 0
        
        signal = Signal(
            agent_type="emergency",
            signal_type="volume_spike",
            priority=Priority.URGENT,
            message=f"⚡ ВСПЛЕСК ОБЪЕМА на {symbol}!\n"
                   f"Текущий объем: {volume:.2f}\n"
                   f"Средний объем: {avg_volume:.2f}\n"
                   f"Увеличение: {spike_ratio:.1f}x\n"
                   f"Цена: {price:.4f}",
            symbol=symbol,
            data={
                'price': price,
                'volume': volume,
                'avg_volume': avg_volume,
                'spike_ratio': spike_ratio,
                'reason': 'Резкое увеличение объема торгов'
            }
        )
        await self.event_router.add_signal(signal)
    
    async def _trigger_price_spike_alert(self, symbol: str, price: float, change: float, direction: str):
        """Алерт о резком изменении цены"""
        emoji = "🚀" if direction == "UP" else "💥"
        action = "BUY" if direction == "UP" else "SELL/EXIT"
        
        signal = Signal(
            agent_type="emergency",
            signal_type="price_spike",
            priority=Priority.CRITICAL,
            message=f"{emoji} РЕЗКОЕ ИЗМЕНЕНИЕ ЦЕНЫ на {symbol}!\n"
                   f"Направление: {direction}\n"
                   f"Изменение: {change*100:.2f}%\n"
                   f"Цена: {price:.4f}\n"
                   f"Рекомендация: {action}",
            symbol=symbol,
            data={
                'price': price,
                'change': change,
                'direction': direction,
                'action': action,
                'reason': f'Резкое движение цены на {change*100:.2f}%'
            }
        )
        await self.event_router.add_signal(signal)
    
    async def _check_liquidity_crisis(self, symbol: str, orderbook: Dict, current_price: float):
        """Проверка кризиса ликвидности"""
        try:
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])
            
            if not bids or not asks:
                return
            
            # Проверяем глубину стакана
            bid_depth = sum([price * amount for price, amount in bids[:5]])
            ask_depth = sum([price * amount for price, amount in asks[:5]])
            
            # Если глубина очень мала - кризис ликвидности
            min_depth = current_price * 1000  # Минимальная глубина
            if bid_depth < min_depth or ask_depth < min_depth:
                signal = Signal(
                    agent_type="emergency",
                    signal_type="liquidity_crisis",
                    priority=Priority.HIGH,
                    message=f"⚠️ КРИЗИС ЛИКВИДНОСТИ на {symbol}!\n"
                           f"Глубина бидов: ${bid_depth:,.0f}\n"
                           f"Глубина асков: ${ask_depth:,.0f}\n"
                           f"Цена: {current_price:.4f}\n"
                           f"Рекомендация: ОСТОРОЖНО при входе/выходе",
                    symbol=symbol,
                    data={
                        'price': current_price,
                        'bid_depth': bid_depth,
                        'ask_depth': ask_depth,
                        'reason': 'Низкая ликвидность в стакане'
                    }
                )
                await self.event_router.add_signal(signal)
        except Exception as e:
            self.logger.error(f"Ошибка проверки ликвидности: {e}", exc_info=True)
    
    async def _check_dump_danger(self, symbol: str, candles: List[Dict], current_price: float):
        """Проверка опасности дампа"""
        try:
            if len(candles) < 5:
                return
            
            # Анализ последних 5 свечей
            recent_candles = candles[-5:]
            
            # Проверяем последовательное падение
            falling_count = 0
            volume_increase = False
            
            for i in range(1, len(recent_candles)):
                if recent_candles[i]['close'] < recent_candles[i-1]['close']:
                    falling_count += 1
                if recent_candles[i]['volume'] > recent_candles[i-1]['volume'] * 1.5:
                    volume_increase = True
            
            # Если 4 из 5 свечей падают и объем растет - опасность дампа
            if falling_count >= 4 and volume_increase:
                price_change = (current_price - recent_candles[0]['close']) / recent_candles[0]['close']
                
                signal = Signal(
                    agent_type="emergency",
                    signal_type="dump_danger",
                    priority=Priority.CRITICAL,
                    message=f"🚨 ОПАСНОСТЬ ДАМПА на {symbol}!\n"
                           f"Падающих свечей: {falling_count}/5\n"
                           f"Изменение цены: {price_change*100:.2f}%\n"
                           f"Объем растет при падении\n"
                           f"Рекомендация: EXIT или SHORT",
                    symbol=symbol,
                    data={
                        'price': current_price,
                        'change': price_change,
                        'falling_candles': falling_count,
                        'reason': 'Последовательное падение с ростом объема'
                    }
                )
                await self.event_router.add_signal(signal)
        except Exception as e:
            self.logger.error(f"Ошибка проверки дампа: {e}", exc_info=True)
    
    async def stop(self):
        """Остановка агента"""
        self.running = False

