"""
check_signals.py - Проверка почему сигналы не отправляются
"""
import sqlite3
from datetime import datetime
from config import config

conn = sqlite3.connect('crypto_analytics.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 60)
print("🔍 АНАЛИЗ СИГНАЛОВ")
print("=" * 60)
print()

# Все сигналы
cursor.execute("SELECT COUNT(*) FROM signals")
total = cursor.fetchone()[0]
print(f"📊 Всего сигналов в БД: {total}")

# По агентам
cursor.execute("""
    SELECT agent_type, COUNT(*) as count 
    FROM signals 
    GROUP BY agent_type
    ORDER BY count DESC
""")
print("\n📈 Сигналы по агентам:")
for row in cursor.fetchall():
    print(f"  {row['agent_type']}: {row['count']}")

# По символам
cursor.execute("""
    SELECT symbol, COUNT(*) as count 
    FROM signals 
    WHERE symbol IS NOT NULL
    GROUP BY symbol
    ORDER BY count DESC
    LIMIT 10
""")
print("\n💰 Топ символов:")
for row in cursor.fetchall():
    is_stable = row['symbol'] in config.stable_coins or row['symbol'].endswith('USDT') and len(row['symbol']) == 4
    stable_mark = " (СТАБИЛЬНАЯ)" if is_stable else ""
    print(f"  {row['symbol']}: {row['count']}{stable_mark}")

# Последние сигналы
cursor.execute("""
    SELECT agent_type, signal_type, symbol, timestamp, sent_to_telegram
    FROM signals
    ORDER BY timestamp DESC
    LIMIT 10
""")
print("\n🕐 Последние 10 сигналов:")
for row in cursor.fetchall():
    time_str = datetime.fromtimestamp(row['timestamp']).strftime('%H:%M:%S')
    sent = "✅" if row['sent_to_telegram'] else "❌"
    print(f"  {time_str} | {row['agent_type']} | {row['signal_type']} | {row['symbol']} | {sent}")

# Статистика отправки
cursor.execute("SELECT COUNT(*) FROM signals WHERE sent_to_telegram = 1")
sent = cursor.fetchone()[0]
print(f"\n📤 Отправлено в Telegram: {sent} из {total} ({sent/total*100 if total > 0 else 0:.1f}%)")

# Проверка стабильных монет
cursor.execute("""
    SELECT COUNT(*) FROM signals 
    WHERE symbol IN ('USDT', 'USDC', 'BUSD') 
    OR (symbol LIKE '%USDT' AND LENGTH(symbol) = 4)
""")
stable_count = cursor.fetchone()[0]
print(f"\n⚠️  Сигналов для стабильных монет: {stable_count} (они фильтруются)")

# Валидные сигналы (не стабильные, достаточной длины)
cursor.execute("""
    SELECT COUNT(*) FROM signals 
    WHERE symbol IS NOT NULL
    AND symbol NOT IN ('USDT', 'USDC', 'BUSD')
    AND LENGTH(symbol) >= 6
""")
valid_count = cursor.fetchone()[0]
print(f"✅ Валидных сигналов (не стабильные, длина >= 6): {valid_count}")

conn.close()

print("\n" + "=" * 60)
print("💡 ВЫВОДЫ:")
print("=" * 60)
if stable_count > total * 0.8:
    print("⚠️  Большинство сигналов для стабильных монет (USDT)")
    print("   → Это нормально, они фильтруются системой")
    print("   → Сигналы для реальных криптовалют появятся при рыночных событиях")
elif sent == 0 and valid_count > 0:
    print("⚠️  Есть валидные сигналы, но они не отправляются")
    print("   → Возможно, уверенность сигналов < 0.5 (min_confidence)")
    print("   → Или сигналы не проходят агрегацию")
else:
    print("✅ Система работает корректно")
    print("   → Сигналы генерируются и сохраняются")
    print("   → Они будут отправляться при достижении порога уверенности")

