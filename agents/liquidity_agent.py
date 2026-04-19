"""Order-book liquidity zones, imbalance, and stop-cluster heuristics."""
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from config import config


class LiquidityAgent:
    def __init__(self, db: Database, event_router: EventRouter, market_agent):
        self.db = db
        self.event_router = event_router
        self.market_agent = market_agent
        self.running = False
        self.logger = get_logger(__name__)
    
    async def start(self):
        """Запуск агента"""
        self.running = True
        await self._analyze_liquidity()
    
    async def _analyze_liquidity(self):
        """Анализ зон ликвидности"""
        while self.running:
            try:
                await asyncio.sleep(config.agent.liquidity_analysis_interval)
                
                for symbol, orderbook in self.market_agent.order_books.items():
                    if not orderbook:
                        continue
                    
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    
                    if not bids or not asks:
                        continue
                    
                    # Анализ ликвидности на уровнях
                    liquidity_zones = self._find_liquidity_zones(bids, asks)
                    
                    # Поиск стоп-кластеров (скопления стоп-лоссов)
                    stop_clusters = self._find_stop_clusters(bids, asks)
                    
                    # Анализ имбаланса стакана
                    imbalance = self._calculate_imbalance(bids, asks)
                    
                    # Сохранение зон ликвидности
                    for zone in liquidity_zones:
                        await self.db.save_liquidity_zone(
                            symbol=symbol,
                            price_level=zone['price'],
                            liquidity_amount=zone['amount'],
                            zone_type=zone['type'],
                            data=zone
                        )
                    
                    # Сигналы на основе ликвидности
                    if abs(imbalance) > 0.3:  # Сильный имбаланс
                        direction = "BUY" if imbalance > 0 else "SELL"
                        signal = Signal(
                            agent_type="liquidity",
                            signal_type="orderbook_imbalance",
                            priority=Priority.MEDIUM,
                            message=(
                                f"Book imbalance on {symbol}: {imbalance:.2%} ({direction})"
                            ),
                            symbol=symbol,
                            data={
                                'imbalance': imbalance,
                                'direction': direction,
                                'liquidity_zones': len(liquidity_zones)
                            }
                        )
                        await self.event_router.add_signal(signal)
                    
                    # Сигналы о стоп-кластерах
                    if stop_clusters:
                        for cluster in stop_clusters:
                            signal = Signal(
                                agent_type="liquidity",
                                signal_type="stop_cluster",
                                priority=Priority.HIGH,
                                message=(
                                    f"Stop cluster on {symbol} near {cluster['price']:.4f}"
                                ),
                                symbol=symbol,
                                data={
                                    'price': cluster['price'],
                                    'liquidity': cluster['liquidity'],
                                    'type': cluster['type']
                                }
                            )
                            await self.event_router.add_signal(signal)
                            
            except Exception as e:
                self.logger.error("Liquidity analysis error: %s", e, exc_info=True)
                await asyncio.sleep(10)
    
    def _find_liquidity_zones(self, bids: List, asks: List) -> List[Dict]:
        """Поиск зон ликвидности"""
        zones = []
        
        # Анализ бидов (поддержка)
        bid_liquidity = {}
        for price, amount in bids[:10]:  # Топ 10 уровней
            # Округляем до значимых уровней
            rounded_price = round(price, 2)
            if rounded_price not in bid_liquidity:
                bid_liquidity[rounded_price] = 0
            bid_liquidity[rounded_price] += amount
        
        # Находим крупные зоны
        for price, amount in bid_liquidity.items():
            if amount > sum(bid_liquidity.values()) * 0.1:  # Более 10% от общей ликвидности
                zones.append({
                    'price': price,
                    'amount': amount,
                    'type': 'support'
                })
        
        # Аналогично для асков (сопротивление)
        ask_liquidity = {}
        for price, amount in asks[:10]:
            rounded_price = round(price, 2)
            if rounded_price not in ask_liquidity:
                ask_liquidity[rounded_price] = 0
            ask_liquidity[rounded_price] += amount
        
        for price, amount in ask_liquidity.items():
            if amount > sum(ask_liquidity.values()) * 0.1:
                zones.append({
                    'price': price,
                    'amount': amount,
                    'type': 'resistance'
                })
        
        return zones
    
    def _find_stop_clusters(self, bids: List, asks: List) -> List[Dict]:
        """Поиск стоп-кластеров"""
        clusters = []
        
        # Стоп-кластеры обычно находятся чуть ниже поддержки (для лонгов)
        # или чуть выше сопротивления (для шортов)
        
        if bids:
            # Ищем кластеры ниже текущей цены (стоп-лоссы для лонгов)
            support_price = bids[0][0]
            # Проверяем ликвидность на 0.5-2% ниже
            for price, amount in bids:
                if support_price * 0.98 <= price < support_price * 0.995:
                    if amount > sum([b[1] for b in bids[:5]]):
                        clusters.append({
                            'price': price,
                            'liquidity': amount,
                            'type': 'long_stop_cluster'
                        })
        
        if asks:
            # Ищем кластеры выше текущей цены (стоп-лоссы для шортов)
            resistance_price = asks[0][0]
            for price, amount in asks:
                if resistance_price * 1.005 <= price <= resistance_price * 1.02:
                    if amount > sum([a[1] for a in asks[:5]]):
                        clusters.append({
                            'price': price,
                            'liquidity': amount,
                            'type': 'short_stop_cluster'
                        })
        
        return clusters
    
    def _calculate_imbalance(self, bids: List, asks: List) -> float:
        """Расчет имбаланса стакана"""
        if not bids or not asks:
            return 0.0
        
        # Суммируем объемы на первых 10 уровнях
        bid_volume = sum([price * amount for price, amount in bids[:10]])
        ask_volume = sum([price * amount for price, amount in asks[:10]])
        
        total_volume = bid_volume + ask_volume
        if total_volume == 0:
            return 0.0
        
        # Имбаланс: положительный = больше покупателей, отрицательный = больше продавцов
        imbalance = (bid_volume - ask_volume) / total_volume
        return imbalance
    
    async def stop(self):
        """Остановка агента"""
        self.running = False

