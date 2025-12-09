# 🚀 Multi-Agent Crypto Analytics System

Мультиагентная крипто-аналитическая система с Telegram уведомлениями и веб-дашбордом для анализа криптовалютного рынка в реальном времени.

## ✨ Возможности

- 📈 **Real-time анализ рынка** через Binance WebSocket
- 🤖 **6 специализированных агентов** для разных типов анализа
- 🎯 **Aggregator Agent** - централизованная агрегация и приоритизация сигналов
- 📊 **Веб-дашборд** с графиками и статистикой
- 🔌 **REST API** для интеграции
- 🔔 **Множественные каналы уведомлений** (Telegram, Discord, Email)
- 💾 **SQLite база данных** для хранения истории
- 📈 **Аналитика и backtesting**
- 📥 **Экспорт данных** (CSV, JSON)

## 🏗️ Архитектура

### Агенты:

1. **MarketAgent** - анализ рыночных данных (свечи, стакан, сделки)
2. **OnChainAgent** - отслеживание whale транзакций
3. **LiquidityAgent** - анализ зон ликвидности и стоп-кластеров
4. **ShitcoinAgent** - поиск пампов/дампов на DEX
5. **EmergencyAgent** - срочные сигналы (всплески объема, резкие изменения цены)
6. **AggregatorAgent** - агрегация всех сигналов в финальные рекомендации (BUY/SELL/EXIT/WAIT)

### Компоненты:

- **EventRouter** - маршрутизация сигналов между агентами
- **Database** - SQLite для хранения данных
- **TelegramBot** - отправка уведомлений
- **HealthCheck** - мониторинг состояния агентов
- **Metrics** - сбор статистики
- **Analytics** - расширенная аналитика и backtesting

## 🚀 Быстрый старт

### 1. Установка зависимостей:

```bash
pip install -r requirements.txt
```

### 2. Настройка конфигурации:

Отредактируйте `config.py` или установите переменные окружения:

```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN="ваш_токен"
$env:TELEGRAM_CHAT_ID="ваш_chat_id"

# Linux/Mac
export TELEGRAM_BOT_TOKEN="ваш_токен"
export TELEGRAM_CHAT_ID="ваш_chat_id"
```

### 3. Запуск системы:

**Минимальный запуск (только основная система):**
```bash
python main.py
```

**Полный запуск (с веб-интерфейсами):**

**Терминал 1 - Основная система:**
```bash
python main.py
```

**Терминал 2 - REST API:**
```bash
python web/api.py
```

**Терминал 3 - Веб-дашборд:**
```bash
python web/dashboard_enhanced.py
```

**Или используйте скрипт (Windows):**
```bash
START.bat
```

### 4. Откройте веб-дашборд:

```
http://localhost:8000
```

## 📊 Веб-интерфейсы

### Веб-дашборд:
- **Базовый:** `web/dashboard.py` → http://localhost:8000
- **Расширенный:** `web/dashboard_enhanced.py` → http://localhost:8000
  - Поиск и фильтры
  - Графики сигналов
  - Статистика по агентам
  - Экспорт данных

### REST API:
- **API:** `web/api.py` → http://localhost:8001
- **Документация:** http://localhost:8001/docs

## 🔔 Уведомления

### Telegram (встроено):
- Настройте в `config.py`
- Сигналы отправляются автоматически

### Discord (опционально):
```python
from bot.discord_notifier import DiscordNotifier
discord = DiscordNotifier(webhook_url="YOUR_WEBHOOK_URL")
```

### Email (опционально):
```python
from bot.email_notifier import EmailNotifier
email = EmailNotifier(
    smtp_server="smtp.gmail.com",
    smtp_port=587,
    username="your_email@gmail.com",
    password="your_password",
    recipients=["recipient@example.com"]
)
```

## 📡 REST API Endpoints

- `GET /api/signals` - Список сигналов с фильтрацией
- `GET /api/signals/{id}` - Конкретный сигнал
- `GET /api/metrics` - Метрики системы
- `GET /api/agents/status` - Статус агентов
- `GET /api/candles` - Свечи по символу
- `GET /api/export/csv` - Экспорт в CSV
- `GET /api/export/json` - Экспорт в JSON
- `GET /api/search` - Поиск по сигналам
- `GET /api/stats/symbols` - Статистика по символам

Полная документация: http://localhost:8001/docs

## 📁 Структура проекта

```
tradingBot/
├── agents/              # Агенты анализа
│   ├── market_agent.py
│   ├── onchain_agent.py
│   ├── liquidity_agent.py
│   ├── shitcoin_agent.py
│   ├── emergency_agent.py
│   └── aggregator_agent.py
├── bot/                 # Уведомления
│   ├── telegram_bot.py
│   ├── discord_notifier.py
│   └── email_notifier.py
├── core/                # Ядро системы
│   ├── database.py
│   ├── event_router.py
│   ├── logger.py
│   ├── metrics.py
│   ├── health_check.py
│   ├── analytics.py
│   ├── utils.py
│   └── rate_limiter.py
├── web/                 # Веб-интерфейсы
│   ├── dashboard.py
│   ├── dashboard_enhanced.py
│   └── api.py
├── config.py            # Конфигурация
├── main.py              # Точка входа
└── requirements.txt     # Зависимости
```

## ⚙️ Конфигурация

Основные настройки в `config.py`:

- `telegram.bot_token` - Токен Telegram бота
- `telegram.chat_id` - Chat ID
- `agent.whale_threshold_usd` - Порог whale транзакций
- `agent.min_confidence` - Минимальная уверенность (0.0-1.0)
- `default_symbols` - Символы для отслеживания
- `stable_coins` - Стабильные монеты (фильтруются)

## 📊 База данных

SQLite база данных (`crypto_analytics.db`) содержит:

- `candles` - Исторические свечи
- `signals` - Все сигналы от агентов
- `whale_transactions` - Whale транзакции
- `anomalies` - Аномалии рынка
- `liquidity_zones` - Зоны ликвидности

## 🔍 Мониторинг

### Health Checks:
- Автоматический мониторинг состояния агентов
- Логирование проблем
- Статусы: HEALTHY, DEGRADED, UNHEALTHY

### Метрики:
- Статистика по сигналам
- Анализ по агентам, типам, символам
- Отслеживание ошибок
- Uptime мониторинг

### Логи:
- Структурированное логирование в `logs/`
- Ротация логов (10MB, 5 файлов)
- Отдельные логи для каждого модуля

## 🛠️ Разработка

### Запуск тестов:
```bash
python test_run.py
```

### Проверка системы:
```bash
python check_system_status.py
python check_signals.py
```

## 📝 Документация

- `ПОЛНАЯ_ИНСТРУКЦИЯ.md` - Полная инструкция
- `ЗАПУСК_ВСЕГО.md` - Инструкция по запуску
- `СТАТУС_СИСТЕМЫ.md` - Статус и диагностика

## 🔒 Безопасность

- Не коммитьте `config.py` с реальными токенами
- Используйте переменные окружения для чувствительных данных
- `.gitignore` настроен для исключения конфиденциальных файлов

## 📄 Лицензия

MIT License

## 🤝 Вклад

Pull requests приветствуются! Для больших изменений сначала откройте issue для обсуждения.

## 📧 Контакты

GitHub: [OleksandrProtasov](https://github.com/OleksandrProtasov)

---

**Сделано с ❤️ для крипто-трейдеров**
