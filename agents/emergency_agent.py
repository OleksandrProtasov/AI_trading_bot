"""High-priority alerts: volume spikes, fast moves, thin books, dump patterns."""
import asyncio
from typing import Dict, List

from config import config
from core.database import Database
from core.event_router import EventRouter, Priority, Signal
from core.logger import get_logger
from core.utils import is_stable_coin


class EmergencyAgent:
    def __init__(self, db: Database, event_router: EventRouter, market_agent, liquidity_agent):
        self.db = db
        self.event_router = event_router
        self.market_agent = market_agent
        self.liquidity_agent = liquidity_agent
        self.running = False
        self.last_prices = {}
        self.logger = get_logger(__name__)
        self.volume_threshold = config.agent.volume_spike_threshold
        self.price_change_threshold = config.agent.price_change_threshold
        self.stable_coins = config.stable_coins

    async def start(self):
        self.running = True
        await self._monitor_emergency_events()

    async def _monitor_emergency_events(self):
        while self.running:
            try:
                await asyncio.sleep(config.agent.emergency_check_interval)
                for symbol in self.market_agent.symbols:
                    await self._check_emergency_conditions(symbol)
            except Exception as e:
                self.logger.error("Emergency monitor error: %s", e, exc_info=True)
                await asyncio.sleep(1)

    async def _check_emergency_conditions(self, symbol: str):
        try:
            if is_stable_coin(symbol, self.stable_coins):
                return

            candles = self.market_agent.candle_data.get(symbol, [])
            if len(candles) < 10:
                return

            current_candle = candles[-1]
            current_price = current_candle["close"]
            current_volume = current_candle["volume"]

            avg_volume = sum(c["volume"] for c in candles[-10:]) / min(len(candles), 10)

            if avg_volume > 0 and current_volume > avg_volume * self.volume_threshold:
                await self._trigger_volume_spike_alert(
                    symbol, current_price, current_volume, avg_volume
                )

            if symbol in self.last_prices:
                price_change = abs(current_price - self.last_prices[symbol]) / self.last_prices[
                    symbol
                ]
                if price_change > self.price_change_threshold:
                    direction = "UP" if current_price > self.last_prices[symbol] else "DOWN"
                    await self._trigger_price_spike_alert(
                        symbol, current_price, price_change, direction
                    )

            self.last_prices[symbol] = current_price

            orderbook = self.market_agent.order_books.get(symbol)
            if orderbook:
                await self._check_liquidity_crisis(symbol, orderbook, current_price)

            await self._check_dump_danger(symbol, candles, current_price)

        except Exception as e:
            self.logger.error(
                "Emergency check failed for %s: %s", symbol, e, exc_info=True
            )

    async def _trigger_volume_spike_alert(
        self, symbol: str, price: float, volume: float, avg_volume: float
    ):
        spike_ratio = volume / avg_volume if avg_volume > 0 else 0
        signal = Signal(
            agent_type="emergency",
            signal_type="volume_spike",
            priority=Priority.URGENT,
            message=(
                f"⚡ VOLUME SPIKE on {symbol}!\n"
                f"Current volume: {volume:.2f}\n"
                f"Avg volume: {avg_volume:.2f}\n"
                f"Ratio: {spike_ratio:.1f}x\n"
                f"Price: {price:.4f}"
            ),
            symbol=symbol,
            data={
                "price": price,
                "volume": volume,
                "avg_volume": avg_volume,
                "spike_ratio": spike_ratio,
                "reason": "Sharp increase in traded volume",
            },
        )
        await self.event_router.add_signal(signal)

    async def _trigger_price_spike_alert(
        self, symbol: str, price: float, change: float, direction: str
    ):
        emoji = "🚀" if direction == "UP" else "💥"
        action = "BUY" if direction == "UP" else "SELL/EXIT"
        signal = Signal(
            agent_type="emergency",
            signal_type="price_spike",
            priority=Priority.CRITICAL,
            message=(
                f"{emoji} SHARP PRICE MOVE on {symbol}!\n"
                f"Direction: {direction}\n"
                f"Change: {change * 100:.2f}%\n"
                f"Price: {price:.4f}\n"
                f"Bias: {action}"
            ),
            symbol=symbol,
            data={
                "price": price,
                "change": change,
                "direction": direction,
                "action": action,
                "reason": f"Price moved {change * 100:.2f}% quickly",
            },
        )
        await self.event_router.add_signal(signal)

    async def _check_liquidity_crisis(self, symbol: str, orderbook: Dict, current_price: float):
        try:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            if not bids or not asks:
                return

            bid_depth = sum(price * amount for price, amount in bids[:5])
            ask_depth = sum(price * amount for price, amount in asks[:5])
            min_depth = current_price * 1000
            if bid_depth < min_depth or ask_depth < min_depth:
                signal = Signal(
                    agent_type="emergency",
                    signal_type="liquidity_crisis",
                    priority=Priority.HIGH,
                    message=(
                        f"⚠️ THIN BOOK on {symbol}!\n"
                        f"Bid depth (top5): ${bid_depth:,.0f}\n"
                        f"Ask depth (top5): ${ask_depth:,.0f}\n"
                        f"Price: {current_price:.4f}\n"
                        f"Slippage risk elevated"
                    ),
                    symbol=symbol,
                    data={
                        "price": current_price,
                        "bid_depth": bid_depth,
                        "ask_depth": ask_depth,
                        "reason": "Low visible depth near touch",
                    },
                )
                await self.event_router.add_signal(signal)
        except Exception as e:
            self.logger.error("Liquidity check error: %s", e, exc_info=True)

    async def _check_dump_danger(self, symbol: str, candles: List[Dict], current_price: float):
        try:
            if len(candles) < 5:
                return
            recent_candles = candles[-5:]
            falling_count = 0
            volume_increase = False
            for i in range(1, len(recent_candles)):
                if recent_candles[i]["close"] < recent_candles[i - 1]["close"]:
                    falling_count += 1
                if recent_candles[i]["volume"] > recent_candles[i - 1]["volume"] * 1.5:
                    volume_increase = True

            if falling_count >= 4 and volume_increase:
                price_change = (
                    current_price - recent_candles[0]["close"]
                ) / recent_candles[0]["close"]
                signal = Signal(
                    agent_type="emergency",
                    signal_type="dump_danger",
                    priority=Priority.CRITICAL,
                    message=(
                        f"🚨 DUMP RISK on {symbol}!\n"
                        f"Down candles: {falling_count}/5\n"
                        f"Price change: {price_change * 100:.2f}%\n"
                        f"Volume rising into selling\n"
                        f"Consider de-risking"
                    ),
                    symbol=symbol,
                    data={
                        "price": current_price,
                        "change": price_change,
                        "falling_candles": falling_count,
                        "reason": "Selling streak with rising volume",
                    },
                )
                await self.event_router.add_signal(signal)
        except Exception as e:
            self.logger.error("Dump pattern check error: %s", e, exc_info=True)

    async def stop(self):
        self.running = False
