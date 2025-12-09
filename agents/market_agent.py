"""
market_agent.py - сбор и анализ рыночных данных через Binance WebSocket
"""
import asyncio
import json
import websockets
from typing import Dict, List, Optional, Callable
from datetime import datetime
import pandas as pd
from core.database import Database
from core.event_router import EventRouter, Signal, Priority
from core.logger import get_logger
from core.utils import validate_price, validate_symbol
from config import config


class MarketAgent:
    def __init__(self, db: Database, event_router: EventRouter, symbols: List[str]):
        self.db = db
        self.event_router = event_router
        # Валидация символов
        self.symbols = []
        for s in symbols:
            try:
                validated = validate_symbol(s).lower()
                self.symbols.append(validated)
            except ValueError:
                continue
        self.running = False
        self.websocket = None
        self.candle_data = {}  # {symbol: [candles]}
        self.order_books = {}  # {symbol: orderbook}
        self.recent_trades = {}  # {symbol: [trades]}
        self.logger = get_logger(__name__)
        
    async def start(self):
        """Запуск агента"""
        self.running = True
        # Запускаем несколько задач параллельно
        await asyncio.gather(
            self._listen_klines(),
            self._listen_orderbook(),
            self._listen_trades(),
            self._analyze_market()
        )
    
    async def _listen_klines(self):
        """Слушаем свечи через WebSocket"""
        streams = [f"{symbol}@kline_1m" for symbol in self.symbols]
        stream_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        while self.running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=config.binance.ping_interval,
                    ping_timeout=config.binance.ping_timeout
                ) as ws:
                    self.logger.info(f"Подключен к Binance WebSocket для свечей ({len(self.symbols)} символов)")
                    async for message in ws:
                        if not self.running:
                            break
                        data = json.loads(message)
                        if 'data' in data:
                            await self._process_kline(data['data'])
            except Exception as e:
                self.logger.error(f"Ошибка WebSocket свечей: {e}", exc_info=True)
                await asyncio.sleep(config.binance.reconnect_delay)
    
    async def _process_kline(self, kline_data: Dict):
        """Обработка свечи"""
        try:
            symbol = kline_data['s']
            k = kline_data['k']
            
            if not k['x']:  # Свеча еще не закрыта
                return
            
            # Сохраняем в БД
            await self.db.save_candle(
                symbol=symbol,
                timeframe='1m',
                timestamp=int(k['t']) // 1000,
                open=float(k['o']),
                high=float(k['h']),
                low=float(k['l']),
                close=float(k['c']),
                volume=float(k['v'])
            )
            
            # Обновляем локальные данные
            if symbol not in self.candle_data:
                self.candle_data[symbol] = []
            
            candle = {
                'timestamp': int(k['t']) // 1000,
                'open': float(k['o']),
                'high': float(k['h']),
                'low': float(k['l']),
                'close': float(k['c']),
                'volume': float(k['v'])
            }
            self.candle_data[symbol].append(candle)
            # Храним только последние 100 свечей
            if len(self.candle_data[symbol]) > 100:
                self.candle_data[symbol] = self.candle_data[symbol][-100:]
                
            except Exception as e:
                self.logger.error(f"Ошибка обработки свечи: {e}", exc_info=True)
    
    async def _listen_orderbook(self):
        """Слушаем стакан заявок"""
        # Binance ограничивает количество стримов в одном соединении
        # Разбиваем на чанки
        chunk_size = config.binance.orderbook_chunk_size
        symbol_chunks = [self.symbols[i:i+chunk_size] for i in range(0, len(self.symbols), chunk_size)]
        
        while self.running:
            tasks = []
            for chunk in symbol_chunks:
                tasks.append(self._listen_orderbook_chunk(chunk))
            
            try:
                await asyncio.gather(*tasks)
            except Exception as e:
                self.logger.error(f"Ошибка в задачах стакана: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    async def _listen_orderbook_chunk(self, symbols_chunk: List[str]):
        """Слушаем стакан для группы символов"""
        streams = [f"{symbol}@depth20@100ms" for symbol in symbols_chunk]
        stream_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        while self.running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=config.binance.ping_interval,
                    ping_timeout=config.binance.ping_timeout
                ) as ws:
                    self.logger.info(f"Подключен к Binance WebSocket для стакана ({len(symbols_chunk)} символов)")
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            if 'data' in data:
                                await self._process_orderbook(data['data'])
                            elif 'stream' in data and 'data' in data:
                                # Альтернативный формат ответа
                                await self._process_orderbook(data['data'])
                        except json.JSONDecodeError as e:
                            self.logger.debug(f"Ошибка парсинга JSON стакана: {e}")
                        except Exception as e:
                            self.logger.error(f"Ошибка обработки сообщения стакана: {e}", exc_info=True)
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning(f"Соединение стакана закрыто: {e}, переподключение...")
                await asyncio.sleep(config.binance.reconnect_delay)
            except Exception as e:
                self.logger.error(f"Ошибка WebSocket стакана: {e}, тип: {type(e).__name__}", exc_info=True)
                await asyncio.sleep(config.binance.reconnect_delay)
    
    async def _process_orderbook(self, orderbook_data: Dict):
        """Обработка стакана"""
        try:
            # Проверяем наличие необходимых ключей
            if not orderbook_data or 's' not in orderbook_data:
                return
            
            symbol = orderbook_data.get('s')
            if not symbol:
                return
            
            # Нормализуем символ (Binance может отправлять в разных регистрах)
            symbol = symbol.upper()
            
            # Проверяем наличие bids и asks
            bids_raw = orderbook_data.get('bids', [])
            asks_raw = orderbook_data.get('asks', [])
            
            if not bids_raw or not asks_raw:
                # Если данных нет, пропускаем обновление
                return
            
            # Обрабатываем bids с проверкой на ошибки
            bids = []
            for b in bids_raw:
                try:
                    if len(b) >= 2:
                        bids.append([float(b[0]), float(b[1])])
                except (ValueError, TypeError, IndexError) as e:
                    self.logger.debug(f"Ошибка обработки bid: {b}, ошибка: {e}")
                    continue
            
            # Обрабатываем asks с проверкой на ошибки
            asks = []
            for a in asks_raw:
                try:
                    if len(a) >= 2:
                        asks.append([float(a[0]), float(a[1])])
                except (ValueError, TypeError, IndexError) as e:
                    self.logger.debug(f"Ошибка обработки ask: {a}, ошибка: {e}")
                    continue
            
            # Сохраняем только если есть валидные данные
            if bids and asks:
                self.order_books[symbol] = {
                    'bids': bids,
                    'asks': asks,
                    'timestamp': datetime.utcnow().timestamp()
                }
            else:
                self.logger.debug(f"Пустой стакан для {symbol}, пропускаем")
                
        except KeyError as e:
            self.logger.debug(f"Отсутствует ключ в данных стакана: {e}")
        except Exception as e:
            self.logger.error(f"Ошибка обработки стакана: {e}", exc_info=True)
    
    async def _listen_trades(self):
        """Слушаем сделки"""
        streams = [f"{symbol}@trade" for symbol in self.symbols]
        stream_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        while self.running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=config.binance.ping_interval,
                    ping_timeout=config.binance.ping_timeout
                ) as ws:
                    self.logger.info("Подключен к Binance WebSocket для сделок")
                    async for message in ws:
                        if not self.running:
                            break
                        data = json.loads(message)
                        if 'data' in data:
                            await self._process_trade(data['data'])
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning(f"Соединение сделок закрыто: {e}, переподключение...")
                await asyncio.sleep(config.binance.reconnect_delay)
            except Exception as e:
                self.logger.error(f"Ошибка WebSocket сделок: {e}", exc_info=True)
                await asyncio.sleep(config.binance.reconnect_delay)
    
    async def _process_trade(self, trade_data: Dict):
        """Обработка сделки"""
        try:
            symbol = trade_data['s']
            if symbol not in self.recent_trades:
                self.recent_trades[symbol] = []
            
            trade = {
                'price': float(trade_data['p']),
                'quantity': float(trade_data['q']),
                'timestamp': int(trade_data['T']) // 1000,
                'is_buyer_maker': trade_data['m']
            }
            self.recent_trades[symbol].append(trade)
            # Храним только последние 100 сделок
            if len(self.recent_trades[symbol]) > 100:
                self.recent_trades[symbol] = self.recent_trades[symbol][-100:]
        except Exception as e:
            self.logger.error(f"Ошибка обработки сделки: {e}", exc_info=True)
    
    async def _analyze_market(self):
        """Анализ рыночных данных"""
        while self.running:
            try:
                await asyncio.sleep(config.agent.market_analysis_interval)
                
                for symbol in self.symbols:
                    if symbol not in self.candle_data or len(self.candle_data[symbol]) < 20:
                        continue
                    
                    candles = self.candle_data[symbol]
                    df = pd.DataFrame(candles)
                    
                    # Анализ тренда
                    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
                    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
                    
                    current_price = df['close'].iloc[-1]
                    ema20 = df['ema20'].iloc[-1]
                    ema50 = df['ema50'].iloc[-1]
                    
                    # Определение тренда
                    if current_price > ema20 > ema50:
                        trend = "BULLISH"
                    elif current_price < ema20 < ema50:
                        trend = "BEARISH"
                    else:
                        trend = "NEUTRAL"
                    
                    # Анализ волатильности
                    df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min()
                    volatility = df['atr'].iloc[-1] / current_price if current_price > 0 else 0
                    
                    # Анализ объемов
                    avg_volume = df['volume'].tail(20).mean()
                    current_volume = df['volume'].iloc[-1]
                    volume_spike = current_volume / avg_volume if avg_volume > 0 else 1
                    
                    # Поиск уровней поддержки/сопротивления
                    support = df['low'].tail(20).min()
                    resistance = df['high'].tail(20).max()
                    
                    # Сигналы на основе анализа
                    if volume_spike > 2.0 and trend == "BULLISH":
                        signal = Signal(
                            agent_type="market",
                            signal_type="volume_spike",
                            priority=Priority.MEDIUM,
                            message=f"Всплеск объема на {symbol}: {volume_spike:.2f}x среднего. Тренд: {trend}",
                            symbol=symbol,
                            data={
                                'price': current_price,
                                'volume': current_volume,
                                'volume_spike': volume_spike,
                                'trend': trend,
                                'support': support,
                                'resistance': resistance
                            }
                        )
                        await self.event_router.add_signal(signal)
                    
                    if volatility > 0.05:  # Высокая волатильность
                        signal = Signal(
                            agent_type="market",
                            signal_type="high_volatility",
                            priority=Priority.MEDIUM,
                            message=f"Высокая волатильность на {symbol}: {volatility*100:.2f}%",
                            symbol=symbol,
                            data={
                                'price': current_price,
                                'volatility': volatility,
                                'atr': df['atr'].iloc[-1]
                            }
                        )
                        await self.event_router.add_signal(signal)
                    
                    # Проверка пробоя уровней
                    if current_price > resistance * 0.99:
                        signal = Signal(
                            agent_type="market",
                            signal_type="resistance_break",
                            priority=Priority.HIGH,
                            message=f"Пробой сопротивления на {symbol}: {current_price:.4f} > {resistance:.4f}",
                            symbol=symbol,
                            data={
                                'price': current_price,
                                'resistance': resistance,
                                'trend': trend
                            }
                        )
                        await self.event_router.add_signal(signal)
                    
                    elif current_price < support * 1.01:
                        signal = Signal(
                            agent_type="market",
                            signal_type="support_break",
                            priority=Priority.HIGH,
                            message=f"Пробой поддержки на {symbol}: {current_price:.4f} < {support:.4f}",
                            symbol=symbol,
                            data={
                                'price': current_price,
                                'support': support,
                                'trend': trend
                            }
                        )
                        await self.event_router.add_signal(signal)
                        
            except Exception as e:
                self.logger.error(f"Ошибка анализа: {e}", exc_info=True)
                await asyncio.sleep(10)
    
    async def stop(self):
        """Остановка агента"""
        self.running = False

