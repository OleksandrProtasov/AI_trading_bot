"""Market data from Binance WebSocket: candles, order book, trades."""
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
        # Symbol validation
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
        """Start concurrent WS and analysis loops."""
        self.running = True
        await asyncio.gather(
            self._listen_klines(),
            self._listen_orderbook(),
            self._listen_trades(),
            self._analyze_market()
        )
    
    async def _listen_klines(self):
        """Stream kline/candle updates."""
        streams = [f"{symbol}@kline_1m" for symbol in self.symbols]
        stream_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        while self.running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=config.binance.ping_interval,
                    ping_timeout=config.binance.ping_timeout
                ) as ws:
                    self.logger.info(
                        "Binance candle WS connected (%s symbols)", len(self.symbols)
                    )
                    async for message in ws:
                        if not self.running:
                            break
                        data = json.loads(message)
                        if 'data' in data:
                            await self._process_kline(data['data'])
            except Exception as e:
                self.logger.error("Candle WebSocket error: %s", e, exc_info=True)
                await asyncio.sleep(config.binance.reconnect_delay)
    
    async def _process_kline(self, kline_data: Dict):
        """Persist closed candle and refresh buffers."""
        try:
            symbol = kline_data['s']
            k = kline_data['k']
            
            if not k["x"]:  # candle not closed
                return
            
            # persist
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
            
            # local buffers
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
            # keep last 100 candles
            if len(self.candle_data[symbol]) > 100:
                self.candle_data[symbol] = self.candle_data[symbol][-100:]
                
        except Exception as e:
            self.logger.error("Candle handling error: %s", e, exc_info=True)
    
    async def _listen_orderbook(self):
        """Depth streams (chunked because Binance multiplex limits apply)."""
        chunk_size = config.binance.orderbook_chunk_size
        symbol_chunks = [self.symbols[i:i+chunk_size] for i in range(0, len(self.symbols), chunk_size)]
        
        while self.running:
            tasks = []
            for chunk in symbol_chunks:
                tasks.append(self._listen_orderbook_chunk(chunk))
            
            try:
                await asyncio.gather(*tasks)
            except Exception as e:
                self.logger.error("Order book task error: %s", e, exc_info=True)
                await asyncio.sleep(5)
    
    async def _listen_orderbook_chunk(self, symbols_chunk: List[str]):
        """Depth stream for one symbol chunk."""
        streams = [f"{symbol}@depth20@100ms" for symbol in symbols_chunk]
        stream_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        while self.running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=config.binance.ping_interval,
                    ping_timeout=config.binance.ping_timeout
                ) as ws:
                    self.logger.info(
                        "Binance depth WS connected (%s symbols)", len(symbols_chunk)
                    )
                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            if 'data' in data:
                                await self._process_orderbook(data['data'])
                            elif 'stream' in data and 'data' in data:
                                # alternate payload shape
                                await self._process_orderbook(data['data'])
                        except json.JSONDecodeError as e:
                            self.logger.debug("Depth JSON parse error: %s", e)
                        except Exception as e:
                            self.logger.error("Depth message error: %s", e, exc_info=True)
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning("Depth WS closed (%s), reconnecting...", e)
                await asyncio.sleep(config.binance.reconnect_delay)
            except Exception as e:
                self.logger.error(
                    "Depth WebSocket error: %s (%s)", e, type(e).__name__, exc_info=True
                )
                await asyncio.sleep(config.binance.reconnect_delay)
    
    async def _process_orderbook(self, orderbook_data: Dict):
        """Normalize depth snapshot into order_books."""
        try:
            # required keys
            if not orderbook_data or 's' not in orderbook_data:
                return
            
            symbol = orderbook_data.get('s')
            if not symbol:
                return
            
            # normalize symbol casing
            symbol = symbol.upper()
            
            # bids / asks
            bids_raw = orderbook_data.get('bids', [])
            asks_raw = orderbook_data.get('asks', [])
            
            if not bids_raw or not asks_raw:
                # skip if empty legs
                return
            
            # parse bids
            bids = []
            for b in bids_raw:
                try:
                    if len(b) >= 2:
                        bids.append([float(b[0]), float(b[1])])
                except (ValueError, TypeError, IndexError) as e:
                    self.logger.debug("Bid parse error %s: %s", b, e)
                    continue
            
            # parse asks
            asks = []
            for a in asks_raw:
                try:
                    if len(a) >= 2:
                        asks.append([float(a[0]), float(a[1])])
                except (ValueError, TypeError, IndexError) as e:
                    self.logger.debug("Ask parse error %s: %s", a, e)
                    continue
            
            # persist non-empty book
            if bids and asks:
                self.order_books[symbol] = {
                    'bids': bids,
                    'asks': asks,
                    'timestamp': datetime.utcnow().timestamp()
                }
            else:
                self.logger.debug("Empty book for %s, skip", symbol)
                
        except KeyError as e:
            self.logger.debug("Missing depth field: %s", e)
        except Exception as e:
            self.logger.error("Depth handling error: %s", e, exc_info=True)
    
    async def _listen_trades(self):
        """Agg-trade stream."""
        streams = [f"{symbol}@trade" for symbol in self.symbols]
        stream_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        while self.running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=config.binance.ping_interval,
                    ping_timeout=config.binance.ping_timeout
                ) as ws:
                    self.logger.info("Binance trades WS connected")
                    async for message in ws:
                        if not self.running:
                            break
                        data = json.loads(message)
                        if 'data' in data:
                            await self._process_trade(data['data'])
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning("Trades WS closed (%s), reconnecting...", e)
                await asyncio.sleep(config.binance.reconnect_delay)
            except Exception as e:
                self.logger.error("Trades WebSocket error: %s", e, exc_info=True)
                await asyncio.sleep(config.binance.reconnect_delay)
    
    async def _process_trade(self, trade_data: Dict):
        """Append executed trade to rolling buffer."""
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
            # keep last 100 trades
            if len(self.recent_trades[symbol]) > 100:
                self.recent_trades[symbol] = self.recent_trades[symbol][-100:]
        except Exception as e:
            self.logger.error("Trade handling error: %s", e, exc_info=True)
    
    async def _analyze_market(self):
        """Periodic TA-style heuristics and signal emission."""
        while self.running:
            try:
                await asyncio.sleep(config.agent.market_analysis_interval)
                
                for symbol in self.symbols:
                    if symbol not in self.candle_data or len(self.candle_data[symbol]) < 20:
                        continue
                    
                    candles = self.candle_data[symbol]
                    df = pd.DataFrame(candles)
                    
                    # trend stats
                    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
                    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
                    
                    current_price = df['close'].iloc[-1]
                    ema20 = df['ema20'].iloc[-1]
                    ema50 = df['ema50'].iloc[-1]
                    
                    # label trend
                    if current_price > ema20 > ema50:
                        trend = "BULLISH"
                    elif current_price < ema20 < ema50:
                        trend = "BEARISH"
                    else:
                        trend = "NEUTRAL"
                    
                    # volatility
                    df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min()
                    volatility = df['atr'].iloc[-1] / current_price if current_price > 0 else 0
                    
                    # volume spike
                    avg_volume = df['volume'].tail(20).mean()
                    current_volume = df['volume'].iloc[-1]
                    volume_spike = current_volume / avg_volume if avg_volume > 0 else 1
                    
                    # support / resistance
                    support = df['low'].tail(20).min()
                    resistance = df['high'].tail(20).max()
                    
                    # emit signals
                    if volume_spike > 2.0 and trend == "BULLISH":
                        signal = Signal(
                            agent_type="market",
                            signal_type="volume_spike",
                            priority=Priority.MEDIUM,
                            message=(
                                f"Volume spike on {symbol}: {volume_spike:.2f}x avg. "
                                f"Trend: {trend}"
                            ),
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
                    
                    if volatility > 0.05:
                        signal = Signal(
                            agent_type="market",
                            signal_type="high_volatility",
                            priority=Priority.MEDIUM,
                            message=f"High volatility on {symbol}: {volatility * 100:.2f}%",
                            symbol=symbol,
                            data={
                                'price': current_price,
                                'volatility': volatility,
                                'atr': df['atr'].iloc[-1]
                            }
                        )
                        await self.event_router.add_signal(signal)
                    
                    # level breaks
                    if current_price > resistance * 0.99:
                        signal = Signal(
                            agent_type="market",
                            signal_type="resistance_break",
                            priority=Priority.HIGH,
                            message=(
                                f"Resistance break on {symbol}: "
                                f"{current_price:.4f} > {resistance:.4f}"
                            ),
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
                            message=(
                                f"Support break on {symbol}: "
                                f"{current_price:.4f} < {support:.4f}"
                            ),
                            symbol=symbol,
                            data={
                                'price': current_price,
                                'support': support,
                                'trend': trend
                            }
                        )
                        await self.event_router.add_signal(signal)
                        
            except Exception as e:
                self.logger.error("Market analysis error: %s", e, exc_info=True)
                await asyncio.sleep(10)
    
    async def stop(self):
        """Stop loops."""
        self.running = False

