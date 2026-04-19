# Multi-Agent Crypto Analytics

Real-time crypto market monitoring with specialized agents, signal aggregation, SQLite storage, optional Telegram alerts, a FastAPI REST API, and a web dashboard.

## Features

- **Binance WebSocket** — candles, order book, trades for configured symbols  
- **Six agents** — market, on-chain (DEX volume / whale-style heuristics), liquidity, memecoin scanner, emergency alerts, aggregator  
- **Aggregator** — combines inputs into weighted actions: BUY / SELL / EXIT / WAIT  
- **SQLite** — signals and market data  
- **Dashboard** — `web/dashboard_enhanced.py` (filters, charts, export)  
- **REST API** — `web/api.py` (OpenAPI at `/docs`)  
- **Notifications** — Telegram (primary), optional Discord / Email modules  

This project is **analytics and alerting**, not an automated exchange execution engine.

## Requirements

- Python 3.10+  
- `pip install -r requirements.txt`  

## Configuration

1. Copy the example config and edit values (or use environment variables):

```bash
copy config.py.example config.py
```

On Linux/macOS:

```bash
cp config.py.example config.py
```

2. Set at minimum:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Destination chat or channel ID |

Optional: `LOG_LEVEL` (default `INFO`).

`config.py` is listed in `.gitignore` — do not commit secrets.

## Run

**Core system (agents + router + health checks):**

```bash
python main.py
```

**REST API** (default port `8001`):

```bash
python web/api.py
```

**Dashboard** (default port `8000`):

```bash
python web/dashboard_enhanced.py
```

**Windows helper** (starts API and dashboard in separate windows, then `main.py`):

```bash
START.bat
```

## URLs

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:8000 |
| API | http://localhost:8001 |
| OpenAPI docs | http://localhost:8001/docs |

## Project layout

```
agents/          # Market, on-chain, liquidity, shitcoin, emergency, aggregator
bot/             # Telegram, Discord, Email helpers
core/            # DB, event router, metrics, health, WebSockets, analytics
web/             # FastAPI API and dashboard
tests/           # Pytest smoke tests
main.py          # Entry point
config.py        # Local config (create from example; gitignored)
```

## Tests

```bash
pytest
```

## Utility scripts

| Script | Purpose |
|--------|---------|
| `check_system_status.py` | Dependency, config, DB, logs, ports |
| `check_agents.py` | Agent-related DB checks |
| `check_signals.py` | Signal statistics |
| `test_run.py` | Dry run with mock Telegram (long-running) |
| `setup_telegram.py` / `get_chat_id.py` | Telegram setup helpers |

## License

Use at your own risk. Not financial advice.
