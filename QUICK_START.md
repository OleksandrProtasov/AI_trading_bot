# 🚀 Быстрый старт - Тестирование системы

## Вариант 1: Тестовый запуск (без Telegram)

Для быстрого теста без настройки Telegram:

```bash
python test_run.py
```

Этот скрипт:
- ✅ Работает без Telegram (выводит сообщения в консоль)
- ✅ Использует только 3 символа (BTC, ETH, BNB) для быстрого теста
- ✅ Автоматически останавливается через 5 минут
- ✅ Показывает все сигналы в консоли

## Вариант 2: Полный запуск (с Telegram)

### Шаг 1: Установка зависимостей

```bash
pip install -r requirements.txt
```

Если возникают ошибки, установите по отдельности:
```bash
pip install websockets aiohttp pandas numpy python-telegram-bot python-dotenv
```

### Шаг 2: Настройка Telegram (опционально)

Если хотите получать сообщения в Telegram:

1. Создайте бота через [@BotFather](https://t.me/BotFather) в Telegram
2. Получите токен бота
3. Узнайте свой Chat ID:
   - Напишите боту [@userinfobot](https://t.me/userinfobot)
   - Или отправьте сообщение своему боту и используйте API: `https://api.telegram.org/bot<TOKEN>/getUpdates`

### Шаг 3: Запуск с Telegram

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="ваш_токен"
$env:TELEGRAM_CHAT_ID="ваш_chat_id"
python main.py
```

**Linux/Mac:**
```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
export TELEGRAM_CHAT_ID="ваш_chat_id"
python main.py
```

**Или создайте файл `.env`:**
```
TELEGRAM_BOT_TOKEN=ваш_токен
TELEGRAM_CHAT_ID=ваш_chat_id
```

### Шаг 4: Запуск без Telegram

Если не настроили Telegram, система все равно будет работать, но сигналы не будут отправляться:

```bash
python main.py
```

## Что вы увидите:

1. **Инициализация** - все компоненты запускаются
2. **Подключение к Binance** - WebSocket соединения
3. **Сигналы в реальном времени**:
   - От отдельных агентов (Market, OnChain, Liquidity, etc.)
   - От AggregatorAgent (финальные решения BUY/SELL/EXIT)

## Остановка системы:

Нажмите `Ctrl+C` для корректной остановки

## Проверка работы:

После запуска вы должны увидеть:
- ✅ Подключения к Binance WebSocket
- ✅ Сохранение данных в SQLite (`crypto_analytics.db`)
- ✅ Сигналы от агентов (в консоль или Telegram)
- ✅ Агрегированные сигналы от AggregatorAgent

## Возможные проблемы:

### Ошибка импорта модулей
```bash
# Убедитесь, что вы в корневой директории проекта
cd D:\tradingBot
python main.py
```

### Ошибка WebSocket подключения
- Проверьте интернет соединение
- Binance может временно блокировать запросы - подождите и попробуйте снова

### Ошибка Telegram
- Проверьте правильность токена и Chat ID
- Убедитесь, что бот запущен в BotFather

### База данных заблокирована
- Закройте другие программы, использующие `crypto_analytics.db`
- Или удалите файл и создайте заново

## Логи и отладка:

Все ошибки выводятся в консоль с префиксами:
- `[MarketAgent]` - ошибки Market Agent
- `[OnChainAgent]` - ошибки OnChain Agent
- `[AggregatorAgent]` - ошибки Aggregator Agent
- `[EventRouter]` - ошибки маршрутизации
- `[TelegramBot]` - ошибки Telegram

## Следующие шаги:

1. Запустите тестовый режим: `python test_run.py`
2. Проверьте работу всех агентов
3. Настройте Telegram для получения уведомлений
4. Запустите полную версию: `python main.py`
5. Наблюдайте за сигналами и анализируйте результаты

