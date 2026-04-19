"""Interactive helper: resolve TELEGRAM_CHAT_ID after you message your bot."""
import asyncio
import os

from telegram import Bot


async def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in the environment, then re-run this script.")
        return

    print("=" * 60)
    print("Telegram chat ID lookup")
    print("=" * 60)

    try:
        bot = Bot(token)
        bot_info = await bot.get_me()
        print(f"\nBot OK: @{bot_info.username} ({bot_info.first_name})")
        print("\nSteps:")
        print(f"  1) Open Telegram and DM @{bot_info.username}")
        print("  2) Send any message (e.g. /start)")
        print("  3) Press Enter here...")
        input()
        print("\nFetching updates...")
        updates = await bot.get_updates()
        if not updates:
            print("No updates yet — send a message to the bot and try again.")
            return
        last = updates[-1]
        if not last.message:
            print("Last update has no message payload.")
            return
        chat = last.message.chat
        chat_id = chat.id
        username = chat.username or chat.first_name or "(no username)"
        print("\n" + "=" * 60)
        print("CHAT ID")
        print("=" * 60)
        print(f"  User: {username}")
        print(f"  chat.id: {chat_id}")
        print("\nSet in PowerShell:")
        print(f'  $env:TELEGRAM_CHAT_ID="{chat_id}"')
        print("Or add to config.py / .env as documented in README.md")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
