"""
test_telegram_send.py - Тест отправки сообщения в Telegram
"""
import asyncio
from bot.telegram_bot import TelegramBot
from config import config

async def test():
    print("Тестирование отправки сообщения в Telegram...")
    print(f"Токен: {config.telegram.bot_token[:20]}...")
    print(f"Chat ID: {config.telegram.chat_id}")
    
    try:
        bot = TelegramBot(config.telegram.bot_token, config.telegram.chat_id)
        await bot.send_daily_report("🧪 Тестовое сообщение - система работает!")
        print("✅ Сообщение отправлено успешно!")
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())

