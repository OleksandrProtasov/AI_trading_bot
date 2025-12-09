"""
get_chat_id.py - простой скрипт для получения Chat ID
"""
import asyncio
from telegram import Bot

# Ваш токен бота
BOT_TOKEN = "7614499901:AAGtqU6zyZCtIfQmS87HH9b-X3jEbm23SvA"

async def main():
    print("=" * 60)
    print("🔍 Получение Chat ID")
    print("=" * 60)
    
    try:
        bot = Bot(BOT_TOKEN)
        
        # Проверяем подключение
        bot_info = await bot.get_me()
        print(f"\n✅ Бот подключен!")
        print(f"   Имя: {bot_info.first_name}")
        print(f"   Username: @{bot_info.username}")
        
        print("\n" + "=" * 60)
        print("📝 ИНСТРУКЦИЯ:")
        print("=" * 60)
        print("1. Откройте Telegram")
        print(f"2. Найдите бота: @{bot_info.username}")
        print("3. Отправьте боту ЛЮБОЕ сообщение (например: /start или 'Привет')")
        print("4. Нажмите Enter здесь, чтобы продолжить...")
        print("=" * 60)
        
        input("\nНажмите Enter после отправки сообщения боту...")
        
        # Получаем обновления
        print("\n🔍 Ищу ваше сообщение...")
        updates = await bot.get_updates()
        
        if updates:
            # Берем последнее обновление
            last_update = updates[-1]
            if last_update.message:
                chat = last_update.message.chat
                chat_id = chat.id
                username = chat.username or chat.first_name or "Не указано"
                
                print("\n" + "=" * 60)
                print("✅ CHAT ID НАЙДЕН!")
                print("=" * 60)
                print(f"   Username: {username}")
                print(f"   Chat ID: {chat_id}")
                print("=" * 60)
                
                print("\n📋 Скопируйте этот Chat ID и:")
                print("   1. Откройте файл main.py")
                print("   2. Найдите строку 23:")
                print("      TELEGRAM_CHAT_ID = os.getenv(\"TELEGRAM_CHAT_ID\", \"\")")
                print("   3. Замените на:")
                print(f"      TELEGRAM_CHAT_ID = os.getenv(\"TELEGRAM_CHAT_ID\", \"{chat_id}\")")
                print("\n   ИЛИ установите переменную окружения:")
                print(f"   $env:TELEGRAM_CHAT_ID=\"{chat_id}\"  (PowerShell)")
                print(f"   export TELEGRAM_CHAT_ID=\"{chat_id}\"  (Linux/Mac)")
                
                return chat_id
            else:
                print("\n❌ Не найдено сообщений с chat ID")
        else:
            print("\n❌ Не найдено обновлений")
            print("\n💡 Убедитесь, что:")
            print("   1. Вы отправили сообщение боту")
            print("   2. Бот активен и работает")
            
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        print("\n💡 Альтернативный способ:")
        print("   1. Откройте в браузере:")
        print(f"      https://api.telegram.org/bot{BOT_TOKEN}/getUpdates")
        print("   2. Найдите 'chat':{'id': ЧИСЛО}")
        print("   3. Это число - ваш Chat ID")

if __name__ == "__main__":
    asyncio.run(main())

