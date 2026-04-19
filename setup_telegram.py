"""Create or update `.env` with TELEGRAM_CHAT_ID after you DM the bot."""
import asyncio
import os
from pathlib import Path

from telegram import Bot


async def get_chat_id():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in the environment first.")
        return None

    try:
        bot = Bot(token)
        bot_info = await bot.get_me()
        print(f"Bot OK: @{bot_info.username}")

        print("\nMessage your bot, then press Enter here.")
        input()

        updates = await bot.get_updates()
        if not updates or not updates[-1].message:
            print("No chat message found yet.")
            return None

        chat_id = updates[-1].message.chat.id
        username = updates[-1].message.chat.username or updates[-1].message.chat.first_name
        print(f"\nChat ID: {chat_id} ({username})")

        env_path = Path(".env")
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        def upsert(key: str, value: str):
            nonlocal lines
            prefix = f"{key}="
            out = [ln for ln in lines if not ln.startswith(prefix)]
            out.append(f"{key}={value}")
            lines = out

        upsert("TELEGRAM_BOT_TOKEN", token)
        upsert("TELEGRAM_CHAT_ID", str(chat_id))
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nUpdated {env_path.resolve()}")
        return chat_id
    except Exception as e:
        print(f"Error: {e}")
        return None


if __name__ == "__main__":
    print("Telegram setup — requires TELEGRAM_BOT_TOKEN in environment.")
    asyncio.run(get_chat_id())
