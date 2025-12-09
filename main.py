"""
main.py - точка входа, запускает всех агентов и Telegram-бот
"""
import asyncio
import os
import sys
from core.database import Database
from core.event_router import EventRouter
from core.logger import setup_logger
from core.metrics import Metrics
from core.health_check import HealthCheck
import signal
import sys
from agents.market_agent import MarketAgent
from agents.onchain_agent import OnChainAgent
from agents.liquidity_agent import LiquidityAgent
from agents.shitcoin_agent import ShitcoinAgent
from agents.emergency_agent import EmergencyAgent
from agents.aggregator_agent import AggregatorAgent
from bot.telegram_bot import TelegramBot
from config import config

# Настройка логирования
logger = setup_logger(__name__, config.log_dir)


async def main():
    """Главная функция запуска системы"""
    logger.info("=" * 60)
    logger.info("🚀 Запуск мультиагентной крипто-аналитической системы")
    logger.info("=" * 60)
    
    # Валидация конфигурации
    if not config.validate():
        logger.error("Ошибки в конфигурации. Исправьте и перезапустите.")
        sys.exit(1)
    
    logger.info(f"Конфигурация загружена: {len(config.default_symbols)} символов")
    
    # Инициализация базы данных
    logger.info("📦 Инициализация базы данных...")
    db = Database(config.database.db_path)
    
    # Инициализация метрик
    metrics = Metrics(db)
    
    # Инициализация health checks
    health_check = HealthCheck()
    
    # Инициализация Telegram бота
    logger.info("🤖 Инициализация Telegram бота...")
    telegram_bot = None
    if config.telegram.bot_token and config.telegram.chat_id:
        try:
            telegram_bot = TelegramBot(config.telegram.bot_token, config.telegram.chat_id)
            await telegram_bot.send_daily_report("Система запущена и готова к работе! 🚀")
            logger.info("✅ Telegram бот инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации Telegram бота: {e}", exc_info=True)
            telegram_bot = None
    else:
        logger.warning("Telegram бот не инициализирован (нет токена/chat_id)")
    
    # Инициализация Aggregator Agent
    logger.info("🎯 Инициализация Aggregator Agent...")
    aggregator_agent = AggregatorAgent(db, None, telegram_bot)
    
    # Инициализация Event Router с callback для AggregatorAgent
    logger.info("🔄 Инициализация Event Router...")
    async def aggregator_callback(signal):
        await aggregator_agent.add_signal(signal)
    event_router = EventRouter(db, None, aggregator_callback)
    
    aggregator_agent.event_router = event_router
    
    # Инициализация агентов
    logger.info("🤖 Инициализация агентов...")
    
    logger.info("  📈 Market Agent...")
    market_agent = MarketAgent(db, event_router, config.default_symbols)
    
    logger.info("  🐋 OnChain Agent...")
    onchain_agent = OnChainAgent(db, event_router, config.default_symbols)
    
    logger.info("  💧 Liquidity Agent...")
    liquidity_agent = LiquidityAgent(db, event_router, market_agent)
    
    logger.info("  💩 Shitcoin Agent...")
    shitcoin_agent = ShitcoinAgent(db, event_router)
    
    logger.info("  🚨 Emergency Agent...")
    emergency_agent = EmergencyAgent(db, event_router, market_agent, liquidity_agent)
    
    logger.info("✅ Все агенты инициализированы")
    
    # Регистрация агентов для мониторинга
    health_check.register_agent("market", market_agent)
    health_check.register_agent("onchain", onchain_agent)
    health_check.register_agent("liquidity", liquidity_agent)
    health_check.register_agent("shitcoin", shitcoin_agent)
    health_check.register_agent("emergency", emergency_agent)
    health_check.register_agent("aggregator", aggregator_agent)
    
    # Запуск Event Router
    logger.info("🔄 Запуск Event Router...")
    router_task = asyncio.create_task(event_router.process_signals())
    
    # Запуск health check мониторинга
    health_task = asyncio.create_task(health_check.monitor())
    
    # Запуск всех агентов
    logger.info("🚀 Запуск агентов...")
    logger.info("=" * 60)
    
    # Обработка сигналов для graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Получен сигнал {signum}, инициируем graceful shutdown...")
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
            return_exceptions=True
        )
    except KeyboardInterrupt:
        logger.info("⚠️  Получен сигнал остановки...")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        # Остановка всех компонентов
        logger.info("🛑 Остановка системы...")
        
        market_agent.running = False
        onchain_agent.running = False
        liquidity_agent.running = False
        shitcoin_agent.running = False
        emergency_agent.running = False
        aggregator_agent.running = False
        event_router.running = False
        
        # Graceful shutdown WebSocket соединений
        if hasattr(market_agent, 'websocket') and market_agent.websocket:
            try:
                await market_agent.websocket.close()
            except:
                pass
        
        # Финальный health check
        final_status = await health_check.check_health()
        logger.info(f"Финальный статус агентов:\n{health_check.get_status_summary()}")
        
        await asyncio.sleep(2)
        
        # Финальная статистика
        stats = await metrics.get_statistics(24)
        logger.info(f"Статистика за 24 часа: {stats.get('total_signals', 0)} сигналов")
        
        if telegram_bot:
            try:
                await telegram_bot.send_daily_report("Система остановлена. 👋")
            except:
                pass
        
        logger.info("✅ Система остановлена")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 До свидания!")
    except Exception as e:
        logger.critical(f"Ошибка запуска: {e}", exc_info=True)
        sys.exit(1)

