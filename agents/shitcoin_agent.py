"""
shitcoin_agent.py - анализ DEX токенов, щиткоинов, пампов/дампов
"""
import asyncio
import aiohttp
from typing import Dict, List, Optional
from datetime import datetime
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from core.utils import retry, validate_price, is_stable_coin
from core.rate_limiter import dex_screener_limiter
from config import config


class ShitcoinAgent:
    def __init__(self, db: Database, event_router: EventRouter):
        self.db = db
        self.event_router = event_router
        self.running = False
        self.tracked_tokens = set()
        self.logger = get_logger(__name__)
        self.stable_coins = config.stable_coins
    
    async def start(self):
        """Запуск агента"""
        self.running = True
        await asyncio.gather(
            self._scan_dex_tokens(),
            self._analyze_pump_dump()
        )
    
    @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def _scan_dex_tokens(self):
        """Сканирование DEX токенов через DexScreener"""
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with dex_screener_limiter:
                        # Получаем топ токенов по объему
                        url = f"{config.dexscreener.base_url}/search?q=USDT"
                        timeout = aiohttp.ClientTimeout(total=config.dexscreener.timeout)
                        async with session.get(url, timeout=timeout) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            await self._process_dex_tokens(data)
                        else:
                            self.logger.warning(f"DexScreener API вернул статус {resp.status}")
                
                await asyncio.sleep(config.agent.shitcoin_scan_interval)
            except Exception as e:
                self.logger.error(f"Ошибка сканирования DEX: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def _process_dex_tokens(self, data: Dict):
        """Обработка данных о DEX токенах"""
        try:
            if 'pairs' not in data:
                return
            
            for pair in data['pairs']:
                token_address = pair.get('pairAddress', '')
                token_name = pair.get('baseToken', {}).get('name', 'Unknown')
                symbol = pair.get('baseToken', {}).get('symbol', 'Unknown')
                
                # Валидация символа
                try:
                    symbol = symbol.strip().upper()
                    if len(symbol) < 3 or len(symbol) > 20:
                        continue
                except:
                    continue
                
                # Получаем и валидируем метрики
                try:
                    price_usd = validate_price(pair.get('priceUsd', 0))
                    price_change_24h = float(pair.get('priceChange', {}).get('h24', 0) or 0)
                    volume_24h = float(pair.get('volume', {}).get('h24', 0) or 0)
                    liquidity_usd = float(pair.get('liquidity', {}).get('usd', 0) or 0)
                except (ValueError, TypeError) as e:
                    self.logger.debug(f"Пропущен токен {symbol}: невалидные данные - {e}")
                    continue
                
                # Улучшенная проверка стабильных монет
                if is_stable_coin(symbol, self.stable_coins, price_usd):
                    self.logger.debug(f"Пропущен стабильный токен: {symbol} (цена: {price_usd})")
                    continue
                
                # Фильтруем по критериям щиткоина
                if self._is_shitcoin(pair, price_change_24h, volume_24h, liquidity_usd):
                    if token_address not in self.tracked_tokens:
                        self.tracked_tokens.add(token_address)
                        
                        # Анализ риска
                        risk_level = self._calculate_risk(price_change_24h, volume_24h, liquidity_usd)
                        
                        signal = Signal(
                            agent_type="shitcoin",
                            signal_type="new_shitcoin",
                            priority=Priority.MEDIUM if risk_level < 0.7 else Priority.HIGH,
                            message=f"Обнаружен щиткоин: {symbol} ({token_name})\n"
                                   f"Изменение 24h: {price_change_24h:.2f}%\n"
                                   f"Объем: ${volume_24h:,.0f}\n"
                                   f"Ликвидность: ${liquidity_usd:,.0f}\n"
                                   f"Риск: {risk_level:.2%}",
                            symbol=symbol,
                            data={
                                'price': price_usd,
                                'change_24h': price_change_24h,
                                'volume_24h': volume_24h,
                                'liquidity': liquidity_usd,
                                'risk': risk_level,
                                'chain': pair.get('chainId', 'unknown'),
                                'address': token_address
                            }
                        )
                        await self.event_router.add_signal(signal)
                        
                        # Сохраняем аномалию
                        await self.db.save_anomaly(
                            symbol=symbol,
                            anomaly_type="shitcoin_detected",
                            description=f"Обнаружен щиткоин с изменением {price_change_24h:.2f}%",
                            severity="high" if risk_level > 0.7 else "medium",
                            data={'risk': risk_level, 'volume': volume_24h}
                        )
                
                # Проверка на памп/дамп
                if abs(price_change_24h) > 50:  # Изменение более 50%
                    signal_type = "pump" if price_change_24h > 0 else "dump"
                    priority = Priority.URGENT if abs(price_change_24h) > 100 else Priority.HIGH
                    
                    signal = Signal(
                        agent_type="shitcoin",
                        signal_type=signal_type,
                        priority=priority,
                        message=f"{'🚀 ПАМП' if signal_type == 'pump' else '💥 ДАМП'} на {symbol}!\n"
                               f"Изменение: {price_change_24h:.2f}%\n"
                               f"Объем: ${volume_24h:,.0f}",
                        symbol=symbol,
                        data={
                            'price': price_usd,
                            'change': price_change_24h,
                            'volume': volume_24h,
                            'type': signal_type
                        }
                    )
                    await self.event_router.add_signal(signal)
                    
        except Exception as e:
            self.logger.error(f"Ошибка обработки токенов: {e}", exc_info=True)
    
    def _is_shitcoin(self, pair: Dict, price_change: float, volume: float, liquidity: float) -> bool:
        """Определение, является ли токен щиткоином"""
        # Критерии:
        # 1. Высокая волатильность (>30% за 24ч)
        # 2. Низкая ликвидность (<$100k) или высокая ликвидность с подозрительным объемом
        # 3. Новый токен (можно проверить по дате создания)
        
        high_volatility = abs(price_change) > 30
        low_liquidity = liquidity < 100000
        suspicious_volume = volume > liquidity * 10  # Объем в 10 раз больше ликвидности
        
        return high_volatility and (low_liquidity or suspicious_volume)
    
    def _calculate_risk(self, price_change: float, volume: float, liquidity: float) -> float:
        """Расчет уровня риска (0-1)"""
        risk = 0.0
        
        # Риск от волатильности
        if abs(price_change) > 100:
            risk += 0.4
        elif abs(price_change) > 50:
            risk += 0.3
        elif abs(price_change) > 30:
            risk += 0.2
        
        # Риск от низкой ликвидности
        if liquidity < 50000:
            risk += 0.4
        elif liquidity < 100000:
            risk += 0.2
        
        # Риск от подозрительного объема
        if liquidity > 0 and volume > liquidity * 20:
            risk += 0.2
        
        return min(risk, 1.0)
    
    async def _analyze_pump_dump(self):
        """Анализ паттернов пампов и дампов"""
        while self.running:
            try:
                # Анализ отслеживаемых токенов
                for token_address in list(self.tracked_tokens)[:10]:  # Ограничиваем для API
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with dex_screener_limiter:
                                url = f"{config.dexscreener.base_url}/tokens/{token_address}"
                                timeout = aiohttp.ClientTimeout(total=config.dexscreener.timeout)
                                async with session.get(url, timeout=timeout) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    await self._check_pump_dump_patterns(data)
                    except Exception as e:
                        self.logger.debug(f"Ошибка анализа токена {token_address}: {e}")
                
                await asyncio.sleep(30)  # Проверка каждые 30 секунд
            except Exception as e:
                self.logger.error(f"Ошибка анализа pump/dump: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def _check_pump_dump_patterns(self, data: Dict):
        """Проверка паттернов памп/дамп"""
        try:
            if not data or 'pairs' not in data:
                return
            
            pairs = data.get('pairs', [])
            if not pairs or not isinstance(pairs, list):
                return
            
            for pair in pairs:
                symbol = pair.get('baseToken', {}).get('symbol', 'Unknown')
                price_change_data = pair.get('priceChange', {})
                if isinstance(price_change_data, dict):
                    price_change_5m = float(price_change_data.get('m5', 0) or 0)
                    price_change_1h = float(price_change_data.get('h1', 0) or 0)
                else:
                    price_change_5m = 0
                    price_change_1h = 0
                
                # Быстрый памп (рост >20% за 5 минут)
                if price_change_5m > 20:
                    signal = Signal(
                        agent_type="shitcoin",
                        signal_type="rapid_pump",
                        priority=Priority.URGENT,
                        message=f"⚡ БЫСТРЫЙ ПАМП на {symbol}!\n"
                               f"Рост за 5 минут: {price_change_5m:.2f}%\n"
                               f"Рост за 1 час: {price_change_1h:.2f}%",
                        symbol=symbol,
                        data={
                            'change_5m': price_change_5m,
                            'change_1h': price_change_1h,
                            'type': 'rapid_pump'
                        }
                    )
                    await self.event_router.add_signal(signal)
                
                # Быстрый дамп (падение >20% за 5 минут)
                elif price_change_5m < -20:
                    signal = Signal(
                        agent_type="shitcoin",
                        signal_type="rapid_dump",
                        priority=Priority.URGENT,
                        message=f"💥 БЫСТРЫЙ ДАМП на {symbol}!\n"
                               f"Падение за 5 минут: {price_change_5m:.2f}%\n"
                               f"Падение за 1 час: {price_change_1h:.2f}%",
                        symbol=symbol,
                        data={
                            'change_5m': price_change_5m,
                            'change_1h': price_change_1h,
                            'type': 'rapid_dump'
                        }
                    )
                    await self.event_router.add_signal(signal)
        except Exception as e:
            self.logger.error(f"Ошибка проверки паттернов: {e}", exc_info=True)
    
    async def stop(self):
        """Остановка агента"""
        self.running = False

