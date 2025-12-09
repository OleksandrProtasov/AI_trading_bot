"""
check_agents.py - проверка активности агентов
"""
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict

def check_database():
    """Проверка базы данных на активность агентов"""
    db_path = "crypto_analytics.db"
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        print("=" * 70)
        print("📊 ПРОВЕРКА АКТИВНОСТИ АГЕНТОВ")
        print("=" * 70)
        
        # Проверка таблиц
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"\n✅ Таблицы в БД: {', '.join(tables)}")
        
        # Статистика по сигналам
        print("\n" + "=" * 70)
        print("📈 СТАТИСТИКА СИГНАЛОВ")
        print("=" * 70)
        
        # Всего сигналов
        cursor.execute("SELECT COUNT(*) as total FROM signals")
        total = cursor.fetchone()['total']
        print(f"\n📊 Всего сигналов в БД: {total}")
        
        # Сигналы по агентам
        cursor.execute("""
            SELECT agent_type, COUNT(*) as count 
            FROM signals 
            GROUP BY agent_type 
            ORDER BY count DESC
        """)
        print("\n📋 Сигналы по агентам:")
        agent_stats = {}
        for row in cursor.fetchall():
            agent_stats[row['agent_type']] = row['count']
            print(f"   {row['agent_type']:20s}: {row['count']:5d} сигналов")
        
        # Сигналы за последний час
        hour_ago = int((datetime.utcnow() - timedelta(hours=1)).timestamp())
        cursor.execute("""
            SELECT agent_type, COUNT(*) as count 
            FROM signals 
            WHERE timestamp > ?
            GROUP BY agent_type 
            ORDER BY count DESC
        """, (hour_ago,))
        
        recent_signals = cursor.fetchall()
        if recent_signals:
            print("\n⏰ Сигналы за последний час:")
            for row in recent_signals:
                print(f"   {row['agent_type']:20s}: {row['count']:5d} сигналов")
        else:
            print("\n⏰ Сигналы за последний час: нет")
        
        # Последние 10 сигналов
        print("\n" + "=" * 70)
        print("🔔 ПОСЛЕДНИЕ 10 СИГНАЛОВ")
        print("=" * 70)
        
        cursor.execute("""
            SELECT agent_type, signal_type, symbol, priority, message, timestamp, sent_to_telegram
            FROM signals 
            ORDER BY timestamp DESC 
            LIMIT 10
        """)
        
        signals = cursor.fetchall()
        if signals:
            for i, signal in enumerate(signals, 1):
                timestamp = datetime.fromtimestamp(signal['timestamp'])
                sent = "✅" if signal['sent_to_telegram'] else "❌"
                print(f"\n{i}. {sent} {signal['agent_type']} - {signal['signal_type']}")
                print(f"   Символ: {signal['symbol'] or 'N/A'}")
                print(f"   Приоритет: {signal['priority']}")
                print(f"   Время: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"   Сообщение: {signal['message'][:80]}...")
        else:
            print("\n❌ Нет сигналов в БД")
        
        # Статистика по символам
        print("\n" + "=" * 70)
        print("💰 СТАТИСТИКА ПО СИМВОЛАМ")
        print("=" * 70)
        
        cursor.execute("""
            SELECT symbol, COUNT(*) as count 
            FROM signals 
            WHERE symbol IS NOT NULL
            GROUP BY symbol 
            ORDER BY count DESC 
            LIMIT 10
        """)
        
        symbols = cursor.fetchall()
        if symbols:
            print("\n📊 Топ символов по количеству сигналов:")
            for row in symbols:
                print(f"   {row['symbol']:15s}: {row['count']:5d} сигналов")
        else:
            print("\n❌ Нет данных по символам")
        
        # Свечи
        print("\n" + "=" * 70)
        print("📊 ДАННЫЕ СВЕЧЕЙ")
        print("=" * 70)
        
        cursor.execute("SELECT COUNT(*) as total FROM candles")
        candles_total = cursor.fetchone()['total']
        print(f"\n📈 Всего свечей: {candles_total}")
        
        cursor.execute("""
            SELECT symbol, COUNT(*) as count 
            FROM candles 
            GROUP BY symbol 
            ORDER BY count DESC 
            LIMIT 10
        """)
        
        candles_by_symbol = cursor.fetchall()
        if candles_by_symbol:
            print("\n📊 Свечи по символам:")
            for row in candles_by_symbol:
                print(f"   {row['symbol']:15s}: {row['count']:5d} свечей")
        
        # Whale транзакции
        print("\n" + "=" * 70)
        print("🐋 WHALE ТРАНЗАКЦИИ")
        print("=" * 70)
        
        cursor.execute("SELECT COUNT(*) as total FROM whale_transactions")
        whale_total = cursor.fetchone()['total']
        print(f"\n🐋 Всего whale транзакций: {whale_total}")
        
        # Зоны ликвидности
        print("\n" + "=" * 70)
        print("💧 ЗОНЫ ЛИКВИДНОСТИ")
        print("=" * 70)
        
        cursor.execute("SELECT COUNT(*) as total FROM liquidity_zones")
        liquidity_total = cursor.fetchone()['total']
        print(f"\n💧 Всего зон ликвидности: {liquidity_total}")
        
        # Аномалии
        print("\n" + "=" * 70)
        print("⚠️ АНОМАЛИИ")
        print("=" * 70)
        
        cursor.execute("SELECT COUNT(*) as total FROM anomalies")
        anomalies_total = cursor.fetchone()['total']
        print(f"\n⚠️ Всего аномалий: {anomalies_total}")
        
        # Проверка активности за последние 5 минут
        print("\n" + "=" * 70)
        print("⚡ АКТИВНОСТЬ ЗА ПОСЛЕДНИЕ 5 МИНУТ")
        print("=" * 70)
        
        five_min_ago = int((datetime.utcnow() - timedelta(minutes=5)).timestamp())
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM signals 
            WHERE timestamp > ?
        """, (five_min_ago,))
        
        recent_count = cursor.fetchone()['count']
        if recent_count > 0:
            print(f"\n✅ Активность есть: {recent_count} сигналов за последние 5 минут")
            print("   Система работает! 🚀")
        else:
            print(f"\n⚠️ Нет активности за последние 5 минут")
            print("   Возможно система только запустилась или нет событий")
        
        conn.close()
        
        # Итоговая оценка
        print("\n" + "=" * 70)
        print("📋 ИТОГОВАЯ ОЦЕНКА")
        print("=" * 70)
        
        if total > 0:
            print(f"\n✅ Система работает!")
            print(f"   - Всего сигналов: {total}")
            print(f"   - Активных агентов: {len(agent_stats)}")
            print(f"   - Свечей собрано: {candles_total}")
            if recent_count > 0:
                print(f"   - Активность: ЕСТЬ (последние 5 минут)")
            else:
                print(f"   - Активность: НЕТ (возможно нет событий)")
        else:
            print("\n⚠️ Система не генерирует сигналы")
            print("   Проверьте:")
            print("   1. Запущена ли система (python main.py)")
            print("   2. Есть ли подключение к Binance")
            print("   3. Прошло ли достаточно времени для генерации сигналов")
        
        print("\n" + "=" * 70)
        
    except FileNotFoundError:
        print(f"\n❌ База данных {db_path} не найдена!")
        print("   Запустите систему: python main.py")
    except Exception as e:
        print(f"\n❌ Ошибка при проверке: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_database()

