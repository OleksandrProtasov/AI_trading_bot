"""
test_run.py - тестовый запуск системы без Telegram (для проверки работы)
"""
import asyncio
import os
import sys
from core.database import Database
from core.event_router import EventRouter
from agents.market_agent import MarketAgent
from agents.onchain_agent import OnChainAgent
from agents.liquidity_agent import LiquidityAgent
from agents.shitcoin_agent import ShitcoinAgent
from agents.emergency_agent import EmergencyAgent
from agents.aggregator_agent import AggregatorAgent

# Тестовые символы (меньше для быстрого теста)
TEST_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]


class TestTelegramBot:
    """Мок Telegram бота для тестирования"""
    def __init__(self):
        self.messages = []
    
    async def send_signal(self, signal):
        """Выводит сигнал в консоль вместо отправки в Telegram"""
        print("\n" + "="*60)
        print(f"📨 TELEGRAM SIGNAL (тест)")
        print("="*60)
        print(f"Agent: {signal.agent_type}")
        print(f"Type: {signal.signal_type}")
        print(f"Priority: {signal.priority.value}")
        print(f"Symbol: {signal.symbol}")
        print(f"Message:\n{signal.message}")
        if signal.data:
            print(f"Data: {signal.data}")
        print("="*60 + "\n")
        self.messages.append(signal)
    
    async def send_daily_report(self, report):
        """Выводит отчет в консоль"""
        print("\n" + "="*60)
        print("📊 DAILY REPORT (тест)")
        print("="*60)
        print(report)
        print("="*60 + "\n")


async def main():
    """Тестовая функция запуска"""
    print("=" * 60)
    print("🧪 ТЕСТОВЫЙ РЕЖИМ - Запуск системы")
    print("=" * 60)
    print("⚠️  Telegram отключен, все сообщения выводятся в консоль")
    print("=" * 60)
    
    # Инициализация базы данных
    print("\n📦 Инициализация базы данных...")
    db = Database("test_crypto_analytics.db")
    print("✅ База данных инициализирована")
    
    # Тестовый Telegram бот (мок)
    print("\n🤖 Инициализация тестового Telegram бота...")
    test_bot = TestTelegramBot()
    print("✅ Тестовый бот готов (сообщения в консоль)")
    
    # Инициализация Aggregator Agent
    print("\n🎯 Инициализация Aggregator Agent...")
    aggregator_agent = AggregatorAgent(db, None, test_bot)
    
    # Инициализация Event Router
    print("🔄 Инициализация Event Router...")
    async def aggregator_callback(signal):
        await aggregator_agent.add_signal(signal)
    event_router = EventRouter(db, None, aggregator_callback)
    aggregator_agent.event_router = event_router
    print("✅ Event Router инициализирован")
    
    # Инициализация агентов
    print("\n🤖 Инициализация агентов...")
    print("  📈 Market Agent...")
    market_agent = MarketAgent(db, event_router, TEST_SYMBOLS)
    
    print("  🐋 OnChain Agent...")
    onchain_agent = OnChainAgent(db, event_router, TEST_SYMBOLS)
    
    print("  💧 Liquidity Agent...")
    liquidity_agent = LiquidityAgent(db, event_router, market_agent)
    
    print("  💩 Shitcoin Agent...")
    shitcoin_agent = ShitcoinAgent(db, event_router)
    
    print("  🚨 Emergency Agent...")
    emergency_agent = EmergencyAgent(db, event_router, market_agent, liquidity_agent)
    
    print("✅ Все агенты инициализированы")
    
    # Запуск Event Router
    print("\n🔄 Запуск Event Router...")
    router_task = asyncio.create_task(event_router.process_signals())
    
    # Запуск всех агентов
    print("\n🚀 Запуск системы...")
    print("=" * 60)
    print("Система работает! Ожидайте сигналы...")
    print("Нажмите Ctrl+C для остановки")
    print("=" * 60)
    
    try:
        # Запускаем на ограниченное время для теста (5 минут)
        await asyncio.wait_for(
            asyncio.gather(
                market_agent.start(),
                onchain_agent.start(),
                liquidity_agent.start(),
                shitcoin_agent.start(),
                emergency_agent.start(),
                aggregator_agent.start(),
                router_task
            ),
            timeout=300  # 5 минут
        )
    except asyncio.TimeoutError:
        print("\n⏰ Тест завершен (5 минут)")
    except KeyboardInterrupt:
        print("\n\n⚠️  Получен сигнал остановки...")
    except Exception as e:
        print(f"\n\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Остановка всех компонентов
        print("\n🛑 Остановка системы...")
        
        market_agent.running = False
        onchain_agent.running = False
        liquidity_agent.running = False
        shitcoin_agent.running = False
        emergency_agent.running = False
        aggregator_agent.running = False
        event_router.running = False
        
        await asyncio.sleep(2)
        
        print(f"\n📊 Статистика теста:")
        print(f"  Всего сообщений: {len(test_bot.messages)}")
        print("✅ Система остановлена")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Тест прерван пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка запуска: {e}")
        import traceback
        traceback.print_exc()

