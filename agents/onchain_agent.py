"""
onchain_agent.py - отслеживание ончейн данных, whale транзакций
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


class OnChainAgent:
    def __init__(self, db: Database, event_router: EventRouter, symbols: List[str]):
        self.db = db
        self.event_router = event_router
        self.symbols = symbols
        self.running = False
        self.logger = get_logger(__name__)
        self.whale_threshold_usd = config.agent.whale_threshold_usd
        self.stable_coins = config.stable_coins
    
    async def start(self):
        """Запуск агента"""
        self.running = True
        await asyncio.gather(
            self._track_whale_transactions(),
            self._track_exchange_flows(),
            self._analyze_accumulation()
        )
    
    @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def _track_whale_transactions(self):
        """Отслеживание крупных транзакций"""
        while self.running:
            try:
                # Используем бесплатные API для отслеживания whale транзакций
                async with aiohttp.ClientSession() as session:
                    # Отслеживание через DexScreener
                    for symbol in self.symbols[:5]:  # Ограничиваем для бесплатного API
                        # Пропускаем стабильные монеты
                        if is_stable_coin(symbol, self.stable_coins):
                            continue
                            
                        try:
                            async with dex_screener_limiter:
                                url = f"{config.dexscreener.base_url}/tokens/{symbol}"
                                timeout = aiohttp.ClientTimeout(total=config.dexscreener.timeout)
                                async with session.get(url, timeout=timeout) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    await self._process_dex_data(symbol, data)
                                elif resp.status == 429:
                                    self.logger.warning(f"Rate limit для {symbol}, пропускаем")
                                    await asyncio.sleep(5)
                        except Exception as e:
                            self.logger.debug(f"Ошибка получения данных для {symbol}: {e}")
                
                await asyncio.sleep(config.agent.onchain_check_interval)
            except Exception as e:
                self.logger.error(f"Ошибка отслеживания whale: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def _process_dex_data(self, symbol: str, data: Dict):
        """Обработка данных с DexScreener"""
        try:
            if not data or 'pairs' not in data:
                return
            
            pairs = data.get('pairs', [])
            if not pairs or not isinstance(pairs, list):
                return
            
            for pair in pairs:
                if 'txns' not in pair:
                    continue
                
                txns = pair.get('txns', {})
                if not txns:
                    continue
                    
                # Проверяем крупные транзакции
                if 'h24' in txns:
                    buys = txns['h24'].get('buys', 0)
                    sells = txns['h24'].get('sells', 0)
                    volume_data = pair.get('volume', {})
                    if isinstance(volume_data, dict):
                        volume_usd = float(volume_data.get('h24', 0) or 0)
                    else:
                        volume_usd = float(volume_data or 0)
                    
                    # Валидация объема
                    try:
                        volume_usd = validate_price(volume_usd) if volume_usd else 0
                    except ValueError:
                        continue
                    
                    # Если большой объем - это может быть whale активность
                    if volume_usd > self.whale_threshold_usd:
                        signal = Signal(
                            agent_type="onchain",
                            signal_type="whale_activity",
                            priority=Priority.HIGH,
                            message=f"Whale активность на {symbol}: объем 24h = ${volume_usd:,.0f}",
                            symbol=symbol,
                            data={
                                'volume_usd': volume_usd,
                                'buys': buys,
                                'sells': sells,
                                'chain': pair.get('chainId', 'unknown')
                            }
                        )
                        await self.event_router.add_signal(signal)
                        
                        # Сохраняем в БД
                        await self.db.save_whale_transaction(
                            chain=pair.get('chainId', 'unknown'),
                            token=symbol,
                            from_address="",
                            to_address="",
                            amount=0,
                            value_usd=volume_usd,
                            transaction_type="whale_activity",
                            data={'buys': buys, 'sells': sells}
                        )
        except Exception as e:
            self.logger.error(f"Ошибка обработки DEX данных: {e}", exc_info=True)
    
    async def _track_exchange_flows(self):
        """Отслеживание вводов/выводов на биржи"""
        while self.running:
            try:
                # Здесь можно использовать бесплатные API для отслеживания flows
                # Например, через публичные эндпоинты или агрегаторы
                
                # Симуляция: в реальности здесь будет запрос к API
                await asyncio.sleep(60)  # Проверка каждую минуту
            except Exception as e:
                self.logger.error(f"Ошибка отслеживания flows: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def _analyze_accumulation(self):
        """Анализ accumulation/distribution событий"""
        while self.running:
            try:
                # Анализ паттернов накопления/распределения
                # На основе whale транзакций из БД
                
                await asyncio.sleep(300)  # Анализ каждые 5 минут
            except Exception as e:
                self.logger.error(f"Ошибка анализа accumulation: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def stop(self):
        """Остановка агента"""
        self.running = False

