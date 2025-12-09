"""
check_system_status.py - Проверка статуса системы
"""
import sys
import os
import sqlite3
from pathlib import Path

def check_system():
    """Проверка всех компонентов системы"""
    print("=" * 60)
    print("🔍 ПРОВЕРКА СТАТУСА СИСТЕМЫ")
    print("=" * 60)
    print()
    
    issues = []
    
    # 1. Проверка Python модулей
    print("📦 Проверка зависимостей...")
    required_modules = [
        'asyncio', 'websockets', 'aiohttp', 'pandas', 'numpy',
        'telegram', 'fastapi', 'uvicorn', 'sqlite3'
    ]
    
    missing = []
    for module in required_modules:
        try:
            if module == 'telegram':
                __import__('telegram')
            elif module == 'sqlite3':
                __import__('sqlite3')
            else:
                __import__(module)
            print(f"  ✅ {module}")
        except ImportError:
            print(f"  ❌ {module} - НЕ УСТАНОВЛЕН")
            missing.append(module)
    
    if missing:
        issues.append(f"Отсутствуют модули: {', '.join(missing)}")
    
    print()
    
    # 2. Проверка конфигурации
    print("⚙️  Проверка конфигурации...")
    try:
        from config import config
        if config.telegram.bot_token:
            print(f"  ✅ Telegram токен: установлен")
        else:
            print(f"  ⚠️  Telegram токен: НЕ установлен")
            issues.append("Telegram токен не установлен")
        
        if config.telegram.chat_id:
            print(f"  ✅ Telegram Chat ID: установлен")
        else:
            print(f"  ⚠️  Telegram Chat ID: НЕ установлен")
            issues.append("Telegram Chat ID не установлен")
        
        print(f"  ✅ Символов для отслеживания: {len(config.default_symbols)}")
    except Exception as e:
        print(f"  ❌ Ошибка загрузки конфигурации: {e}")
        issues.append(f"Ошибка конфигурации: {e}")
    
    print()
    
    # 3. Проверка базы данных
    print("💾 Проверка базы данных...")
    db_path = "crypto_analytics.db"
    if os.path.exists(db_path):
        print(f"  ✅ База данных существует: {db_path}")
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Проверка таблиц
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"  ✅ Таблиц в БД: {len(tables)}")
            
            # Проверка сигналов
            if 'signals' in tables:
                cursor.execute("SELECT COUNT(*) FROM signals")
                signal_count = cursor.fetchone()[0]
                print(f"  ✅ Сигналов в БД: {signal_count}")
            
            # Проверка свечей
            if 'candles' in tables:
                cursor.execute("SELECT COUNT(*) FROM candles")
                candle_count = cursor.fetchone()[0]
                print(f"  ✅ Свечей в БД: {candle_count}")
            
            conn.close()
        except Exception as e:
            print(f"  ❌ Ошибка чтения БД: {e}")
            issues.append(f"Ошибка БД: {e}")
    else:
        print(f"  ⚠️  База данных не найдена (будет создана при первом запуске)")
    
    print()
    
    # 4. Проверка логов
    print("📝 Проверка логов...")
    logs_dir = Path("logs")
    if logs_dir.exists():
        log_files = list(logs_dir.glob("*.log"))
        print(f"  ✅ Файлов логов: {len(log_files)}")
        if log_files:
            for log_file in log_files[:5]:  # Показываем первые 5
                size = log_file.stat().st_size
                print(f"    - {log_file.name} ({size} bytes)")
    else:
        print(f"  ⚠️  Папка logs не найдена (будет создана при запуске)")
    
    print()
    
    # 5. Проверка портов
    print("🌐 Проверка портов...")
    import socket
    
    ports_to_check = [8000, 8001]
    for port in ports_to_check:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        if result == 0:
            print(f"  ✅ Порт {port}: ЗАНЯТ (сервис работает)")
        else:
            print(f"  ⚠️  Порт {port}: СВОБОДЕН (сервис не запущен)")
    
    print()
    
    # 6. Итоговый статус
    print("=" * 60)
    if issues:
        print("⚠️  ОБНАРУЖЕНЫ ПРОБЛЕМЫ:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        print()
        print("💡 РЕКОМЕНДАЦИИ:")
        if "Отсутствуют модули" in str(issues):
            print("  → Установите зависимости: pip install -r requirements.txt")
        if "Telegram" in str(issues):
            print("  → Проверьте config.py или установите переменные окружения")
        print()
    else:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ УСПЕШНО!")
        print()
        print("💡 СИСТЕМА ГОТОВА К ЗАПУСКУ:")
        print("  → python main.py")
        print("  → python web/dashboard_enhanced.py")
        print("  → python web/api.py")
    print("=" * 60)

if __name__ == "__main__":
    check_system()

