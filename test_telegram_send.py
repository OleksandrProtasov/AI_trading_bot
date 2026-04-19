"""Manual smoke test: sends one HTML message via configured Telegram bot."""
import asyncio

from bot.telegram_bot import TelegramBot
from config import config


async def test():
    print("Sending test Telegram message...")
    if not config.telegram.bot_token:
        print("Missing TELEGRAM_BOT_TOKEN / config.telegram.bot_token")
        return
    print(f"Token prefix: {config.telegram.bot_token[:8]}…")
    print(f"Chat ID: {config.telegram.chat_id}")

    try:
        bot = TelegramBot(config.telegram.bot_token, config.telegram.chat_id)
        await bot.send_daily_report("Test message — Telegram integration OK.")
        print("Sent.")
    except Exception as e:
        print(f"Send failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(test())
