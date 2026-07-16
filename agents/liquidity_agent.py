"""Order-book liquidity zones, imbalance, and stop-cluster heuristics."""
import asyncio
import time
from typing import Dict, List, Optional
from datetime import datetime
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from core.market_context import BookMetrics, analyze_orderbook
from config import config


class LiquidityAgent:
    def __init__(self, db: Database, event_router: EventRouter, market_agent):
        self.db = db
        self.event_router = event_router
        self.market_agent = market_agent
        self.running = False
        self.logger = get_logger(__name__)
        self._book_cache: Dict[str, BookMetrics] = {}
        self._book_cache_ts: Dict[str, float] = {}
        self._cluster_cache: Dict[str, List[Dict]] = {}
        self._cluster_cache_ts: Dict[str, float] = {}
    
    def get_stop_clusters(self, symbol: str) -> List[Dict]:
        """Cached stop clusters (max 120s stale)."""
        sym = symbol.upper()
        ts = self._cluster_cache_ts.get(sym, 0)
        if time.time() - ts > 120:
            return []
        return list(self._cluster_cache.get(sym) or [])

    def get_liquidity_levels_for_tp(
        self, symbol: str, action: str, entry: float
    ) -> List[Dict]:
        """Book zones + stop clusters for TP selection."""
        sym = symbol.upper()
        act = (action or "").upper()
        out: List[Dict] = []
        for cluster in self.get_stop_clusters(sym):
            out.append(cluster)
        ob = self.market_agent.order_books.get(sym) or self.market_agent.order_books.get(symbol)
        if ob and ob.get("bids") and ob.get("asks"):
            for zone in self._find_liquidity_zones(ob.get("bids"), ob.get("asks")):
                zt = zone.get("type") or ""
                px = float(zone.get("price") or 0)
                if act == "BUY" and zt == "resistance" and px > entry:
                    out.append({"price": px, "type": "resistance", "zone_type": "resistance"})
                elif act == "SELL" and zt == "support" and px < entry:
                    out.append({"price": px, "type": "support", "zone_type": "support"})
        return out

    def get_book_metrics(self, symbol: str, action: str = "BUY") -> Optional[BookMetrics]:
        """Latest cached book metrics for scoring (max 120s stale)."""
        sym = symbol.upper()
        ts = self._book_cache_ts.get(sym, 0)
        if time.time() - ts > 120:
            ob = self.market_agent.order_books.get(sym) or self.market_agent.order_books.get(
                symbol
            )
            if ob and ob.get("bids") and ob.get("asks"):
                return self._metrics_from_book(ob.get("bids"), ob.get("asks"), action)
            return None
        cached = self._book_cache.get(sym)
        if cached is not None and time.time() - ts <= 120:
            ob = self.market_agent.order_books.get(sym) or self.market_agent.order_books.get(
                symbol
            )
            if ob and ob.get("bids") and ob.get("asks"):
                return self._metrics_from_book(ob.get("bids"), ob.get("asks"), action)
        return cached

    def _metrics_from_book(self, bids: List, asks: List, action: str) -> BookMetrics:
        try:
            from config import config as cfg

            a = cfg.agent
            return analyze_orderbook(
                bids,
                asks,
                action,
                min_depth_usd=float(getattr(a, "market_min_book_depth_usd", 50_000.0)),
                max_spread_pct=float(getattr(a, "market_max_spread_pct", 0.15)),
                min_imbalance=float(getattr(a, "market_min_book_imbalance", 0.08)),
            )
        except Exception:
            return analyze_orderbook(bids, asks, action)
    
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
                    stop_clusters = self._find_stop_clusters(bids, asks)
                    metrics = self._metrics_from_book(bids, asks, "BUY")
                    self._book_cache[symbol.upper()] = metrics
                    self._book_cache_ts[symbol.upper()] = time.time()
                    self._cluster_cache[symbol.upper()] = stop_clusters
                    self._cluster_cache_ts[symbol.upper()] = time.time()
                    
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
            else:
                self.event_router.ping_health("liquidity")
    
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

