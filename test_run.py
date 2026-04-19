"""Run the stack with a mock Telegram client (console only). Long-running / network."""
import asyncio
import traceback

from agents.aggregator_agent import AggregatorAgent
from agents.emergency_agent import EmergencyAgent
from agents.liquidity_agent import LiquidityAgent
from agents.market_agent import MarketAgent
from agents.onchain_agent import OnChainAgent
from agents.shitcoin_agent import ShitcoinAgent
from core.database import Database
from core.event_router import EventRouter

TEST_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]


class TestTelegramBot:
    """Print-only stand-in for TelegramBot."""

    def __init__(self):
        self.messages = []

    async def send_signal(self, signal):
        print("\n" + "=" * 60)
        print("MOCK TELEGRAM SIGNAL")
        print("=" * 60)
        print(f"Agent: {signal.agent_type}")
        print(f"Type: {signal.signal_type}")
        print(f"Priority: {signal.priority.value}")
        print(f"Symbol: {signal.symbol}")
        print(f"Message:\n{signal.message}")
        if signal.data:
            print(f"Data: {signal.data}")
        print("=" * 60 + "\n")
        self.messages.append(signal)

    async def send_daily_report(self, report):
        print("\n" + "=" * 60)
        print("MOCK TELEGRAM REPORT")
        print("=" * 60)
        print(report)
        print("=" * 60 + "\n")


async def main():
    print("=" * 60)
    print("TEST RUN — Telegram disabled (console mock)")
    print("=" * 60)

    db = Database("test_crypto_analytics.db")
    test_bot = TestTelegramBot()
    aggregator_agent = AggregatorAgent(db, None, test_bot)

    async def aggregator_callback(signal):
        await aggregator_agent.add_signal(signal)

    event_router = EventRouter(db, None, aggregator_callback)
    aggregator_agent.event_router = event_router

    market_agent = MarketAgent(db, event_router, TEST_SYMBOLS)
    onchain_agent = OnChainAgent(db, event_router, TEST_SYMBOLS)
    liquidity_agent = LiquidityAgent(db, event_router, market_agent)
    shitcoin_agent = ShitcoinAgent(db, event_router)
    emergency_agent = EmergencyAgent(db, event_router, market_agent, liquidity_agent)

    router_task = asyncio.create_task(event_router.process_signals())

    print("\nRunning (Ctrl+C to stop, or wait 5 min timeout)...")
    try:
        await asyncio.wait_for(
            asyncio.gather(
                market_agent.start(),
                onchain_agent.start(),
                liquidity_agent.start(),
                shitcoin_agent.start(),
                emergency_agent.start(),
                aggregator_agent.start(),
                router_task,
            ),
            timeout=300,
        )
    except asyncio.TimeoutError:
        print("\n5 minute test window finished.")
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as e:
        print(f"\nError: {e}")
        traceback.print_exc()
    finally:
        for agent in (
            market_agent,
            onchain_agent,
            liquidity_agent,
            shitcoin_agent,
            emergency_agent,
            aggregator_agent,
        ):
            agent.running = False
        event_router.running = False
        await asyncio.sleep(1)
        print(f"\nMock messages captured: {len(test_bot.messages)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted.")
