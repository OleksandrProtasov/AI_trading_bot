"""
setup_telegram.py - скрипт для настройки Telegram
"""
import os
import asyncio
from telegram import Bot

# Токен бота
BOT_TOKEN = "7614499901:AAGtqU6zyZCtIfQmS87HH9b-X3jEbm23SvA"

async def get_chat_id():
    """Получить Chat ID через отправку тестового сообщения"""
    try:
        bot = Bot(BOT_TOKEN)
        
        # Получаем информацию о боте
        bot_info = await bot.get_me()
        print(f"✅ Бот подключен: @{bot_info.username}")
        print(f"   Имя: {bot_info.first_name}")
        
        print("\n📝 Инструкция:")
        print("1. Найдите вашего бота в Telegram: @" + bot_info.username)
        print("2. Отправьте боту любое сообщение (например: /start)")
        print("3. Нажмите Enter здесь, чтобы получить ваш Chat ID...")
        input()
        
        # Получаем обновления
        updates = await bot.get_updates()
        
        if updates:
            # Берем последнее обновление
            last_update = updates[-1]
            chat_id = last_update.message.chat.id
            username = last_update.message.chat.username or last_update.message.chat.first_name
            
            print(f"\n✅ Chat ID найден!")
            print(f"   Username: {username}")
            print(f"   Chat ID: {chat_id}")
            print(f"\n📋 Добавьте в .env файл:")
            print(f"TELEGRAM_CHAT_ID={chat_id}")
            
            # Обновляем .env файл
            env_content = f"""TELEGRAM_BOT_TOKEN={BOT_TOKEN}
TELEGRAM_CHAT_ID={chat_id}
"""
            with open('.env', 'w') as f:
                f.write(env_content)
            
            print(f"\n✅ Файл .env обновлен!")
            return chat_id
        else:
            print("\n❌ Не найдено сообщений от вас боту.")
            print("   Убедитесь, что вы отправили сообщение боту @" + bot_info.username)
            return None
            
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        print("\nАльтернативный способ:")
        print("1. Откройте в браузере:")
        print(f"   https://api.telegram.org/bot{BOT_TOKEN}/getUpdates")
        print("2. Найдите 'chat':{'id': ЧИСЛО}")
        print("3. Это число - ваш Chat ID")
        return None

if __name__ == "__main__":
    print("=" * 60)
    print("🔧 Настройка Telegram бота")
    print("=" * 60)
    asyncio.run(get_chat_id())

