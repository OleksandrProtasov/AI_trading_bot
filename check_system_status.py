"""CLI health check: dependencies, config, database, logs, common ports."""
import os
import socket
import sqlite3
from pathlib import Path


def check_system():
    """Print a human-readable status report."""
    print("=" * 60)
    print("SYSTEM STATUS CHECK")
    print("=" * 60)
    print()

    issues = []

    print("Dependencies...")
    required_modules = [
        "asyncio",
        "websockets",
        "aiohttp",
        "pandas",
        "numpy",
        "telegram",
        "fastapi",
        "uvicorn",
        "sqlite3",
    ]

    missing = []
    for module in required_modules:
        try:
            if module == "telegram":
                __import__("telegram")
            elif module == "sqlite3":
                __import__("sqlite3")
            else:
                __import__(module)
            print(f"  OK  {module}")
        except ImportError:
            print(f"  MISSING  {module}")
            missing.append(module)

    if missing:
        issues.append(f"Missing modules: {', '.join(missing)}")

    print()

    print("Configuration...")
    try:
        from config import config

        if config.telegram.bot_token:
            print("  OK  TELEGRAM_BOT_TOKEN set")
        else:
            print("  WARN  TELEGRAM_BOT_TOKEN empty")
            issues.append("TELEGRAM_BOT_TOKEN not set")

        if config.telegram.chat_id:
            print("  OK  TELEGRAM_CHAT_ID set")
        else:
            print("  WARN  TELEGRAM_CHAT_ID empty")
            issues.append("TELEGRAM_CHAT_ID not set")

        print(f"  OK  symbols configured: {len(config.default_symbols)}")
    except Exception as e:
        print(f"  ERROR  cannot load config: {e}")
        issues.append(f"Config error: {e}")

    print()

    print("Database...")
    db_path = "crypto_analytics.db"
    if os.path.exists(db_path):
        print(f"  OK  database file: {db_path}")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"  OK  tables: {len(tables)}")

            if "signals" in tables:
                cursor.execute("SELECT COUNT(*) FROM signals")
                signal_count = cursor.fetchone()[0]
                print(f"  OK  signals rows: {signal_count}")

            if "candles" in tables:
                cursor.execute("SELECT COUNT(*) FROM candles")
                candle_count = cursor.fetchone()[0]
                print(f"  OK  candles rows: {candle_count}")

            conn.close()
        except Exception as e:
            print(f"  ERROR  database read failed: {e}")
            issues.append(f"Database error: {e}")
    else:
        print("  WARN  database not found yet (created on first run)")

    print()

    print("Logs...")
    logs_dir = Path("logs")
    if logs_dir.exists():
        log_files = list(logs_dir.glob("*.log"))
        print(f"  OK  log files: {len(log_files)}")
        for log_file in log_files[:5]:
            size = log_file.stat().st_size
            print(f"    - {log_file.name} ({size} bytes)")
    else:
        print("  WARN  logs/ missing (created when the app runs)")

    print()

    print("Ports (localhost)...")
    for port in (8000, 8001):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("localhost", port))
        sock.close()
        if result == 0:
            print(f"  OK  {port} in use (service likely up)")
        else:
            print(f"  WARN  {port} free (service not listening)")

    print()
    print("=" * 60)
    if issues:
        print("ISSUES:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        print()
        print("Hints:")
        if any("Missing modules" in x for x in issues):
            print("  - pip install -r requirements.txt")
        if any("TELEGRAM" in x for x in issues):
            print("  - set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID or edit config.py")
        print()
    else:
        print("All checks passed.")
        print()
        print("Typical commands:")
        print("  python main.py")
        print("  python web/dashboard_enhanced.py")
        print("  python web/api.py")
    print("=" * 60)


if __name__ == "__main__":
    check_system()
