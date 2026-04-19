"""
Entry point: starts all agents, event router, health checks, and optional Telegram.
"""
import asyncio
import signal
import sys

from agents.aggregator_agent import AggregatorAgent
from agents.emergency_agent import EmergencyAgent
from agents.liquidity_agent import LiquidityAgent
from agents.market_agent import MarketAgent
from agents.onchain_agent import OnChainAgent
from agents.shitcoin_agent import ShitcoinAgent
from bot.telegram_bot import TelegramBot
from config import config
from core.database import Database
from core.event_router import EventRouter
from core.health_check import HealthCheck
from core.logger import setup_logger
from core.metrics import Metrics

logger = setup_logger(__name__, config.log_dir)


async def main():
    """Run the multi-agent analytics system."""
    logger.info("=" * 60)
    logger.info("Starting multi-agent crypto analytics system")
    logger.info("=" * 60)

    if not config.validate():
        logger.error("Configuration invalid. Fix errors and restart.")
        sys.exit(1)

    logger.info("Loaded config: %s symbols", len(config.default_symbols))

    logger.info("Initializing database...")
    db = Database(config.database.db_path)

    metrics = Metrics(db)
    health_check = HealthCheck()

    logger.info("Initializing Telegram...")
    telegram_bot = None
    if config.telegram.bot_token and config.telegram.chat_id:
        try:
            telegram_bot = TelegramBot(
                config.telegram.bot_token, config.telegram.chat_id
            )
            await telegram_bot.send_daily_report(
                "System is up and running."
            )
            logger.info("Telegram bot ready")
        except Exception as e:
            logger.error("Telegram init failed: %s", e, exc_info=True)
            telegram_bot = None
    else:
        logger.warning("Telegram disabled (missing token or chat id)")

    logger.info("Initializing Aggregator...")
    aggregator_agent = AggregatorAgent(db, None, telegram_bot)

    logger.info("Initializing EventRouter...")

    async def aggregator_callback(sig):
        await aggregator_agent.add_signal(sig)

    event_router = EventRouter(db, None, aggregator_callback)
    aggregator_agent.event_router = event_router

    logger.info("Initializing agents...")
    market_agent = MarketAgent(db, event_router, config.default_symbols)
    onchain_agent = OnChainAgent(db, event_router, config.default_symbols)
    liquidity_agent = LiquidityAgent(db, event_router, market_agent)
    shitcoin_agent = ShitcoinAgent(db, event_router)
    emergency_agent = EmergencyAgent(
        db, event_router, market_agent, liquidity_agent
    )

    logger.info("All agents initialized")

    health_check.register_agent("market", market_agent)
    health_check.register_agent("onchain", onchain_agent)
    health_check.register_agent("liquidity", liquidity_agent)
    health_check.register_agent("shitcoin", shitcoin_agent)
    health_check.register_agent("emergency", emergency_agent)
    health_check.register_agent("aggregator", aggregator_agent)

    logger.info("Starting EventRouter...")
    router_task = asyncio.create_task(event_router.process_signals())
    health_task = asyncio.create_task(health_check.monitor())

    logger.info("Starting agents...")
    logger.info("=" * 60)

    def signal_handler(signum, _frame):
        logger.info("Signal %s received, shutting down...", signum)
        market_agent.running = False
        onchain_agent.running = False
        liquidity_agent.running = False
        shitcoin_agent.running = False
        emergency_agent.running = False
        aggregator_agent.running = False
        event_router.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await asyncio.gather(
            market_agent.start(),
            onchain_agent.start(),
            liquidity_agent.start(),
            shitcoin_agent.start(),
            emergency_agent.start(),
            aggregator_agent.start(),
            router_task,
            health_task,
            return_exceptions=True,
        )
    except KeyboardInterrupt:
        logger.info("Stop requested")
    except Exception as e:
        logger.critical("Fatal error: %s", e, exc_info=True)
    finally:
        logger.info("Stopping system...")

        market_agent.running = False
        onchain_agent.running = False
        liquidity_agent.running = False
        shitcoin_agent.running = False
        emergency_agent.running = False
        aggregator_agent.running = False
        event_router.running = False

        if hasattr(market_agent, "websocket") and market_agent.websocket:
            try:
                await market_agent.websocket.close()
            except Exception:
                pass

        await health_check.check_health()
        logger.info("Final agent status:\n%s", health_check.get_status_summary())

        await asyncio.sleep(2)

        stats = await metrics.get_statistics(24)
        logger.info(
            "Signals (24h window): %s", stats.get("total_signals", 0)
        )

        if telegram_bot:
            try:
                await telegram_bot.send_daily_report("System stopped.")
            except Exception:
                pass

        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Goodbye")
    except Exception as e:
        logger.critical("Startup error: %s", e, exc_info=True)
        sys.exit(1)
