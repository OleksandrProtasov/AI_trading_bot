# Multi-Agent Crypto Analytics — Developer Guide

Python service that ingests **public crypto market data**, runs **concurrent analysis agents**, persists **signals and candles** to **SQLite**, and exposes **Telegram**, a **FastAPI REST API**, and a **browser dashboard**. This document is written **for engineers** integrating, deploying, or extending the codebase.

**Scope:** analytics and alerting only. **No order execution**, no exchange keys required for public WebSocket streams, no embedded ML training pipeline.

---

## 1. System overview

| Layer | Responsibility |
|-------|------------------|
| **Agents** (`agents/`) | Domain-specific loops: market WS, DEX polling, book/liquidity heuristics, emergency thresholds, aggregation. |
| **Event router** (`core/event_router.py`) | Async queue: persist signal → invoke aggregator callback → optional legacy Telegram path. |
| **Persistence** (`core/database.py`) | SQLite: candles, signals, whale rows, anomalies, liquidity zones. |
| **Notifications** (`bot/`) | `TelegramBot` (HTML); optional Discord / Email modules for custom wiring. |
| **Web** (`web/`) | `api.py` — REST + OpenAPI; `dashboard_enhanced.py` — SPA-style dashboard + WS refresh. |
| **Orchestration** (`main.py`) | Loads config, constructs `Database`, `EventRouter`, agents, `HealthCheck`, `Metrics`, starts `asyncio.gather`. |

Entry point: **`python main.py`**. Config: **`config.py`** (gitignored); template **`config.py.example`**.

---

## 2. Agent modules (developer map)

| Module | Inputs | Typical outputs |
|--------|--------|-----------------|
| `market_agent.py` | Binance combined WS (klines, depth, trades) | Candle DB rows, local buffers, TA-style signals (volume spike, volatility, S/R breaks). |
| `onchain_agent.py` | DexScreener HTTP | Volume / flow-style signals (not a full chain indexer). |
| `liquidity_agent.py` | Order books from `MarketAgent` | Imbalance, stop-cluster heuristics, liquidity zone inserts. |
| `shitcoin_agent.py` | DexScreener search / token endpoints | High-volatility DEX pair alerts, pump/dump patterns. |
| `emergency_agent.py` | Candles + books from `MarketAgent` | Volume/price spikes, thin book, dump-pattern signals. |
| `aggregator_agent.py` | Recent signals per symbol via router callback | Weighted BUY / SELL / EXIT / WAIT, dedupe, Telegram HTML, hourly summary. |

All agents share **`Signal`** / **`Priority`** from `core/event_router.py` and push through **`EventRouter.add_signal`**.

---

## 3. Core packages

| Path | Notes |
|------|--------|
| `core/database.py` | `asyncio.Lock`-guarded writes; `save_signal`, `save_candle`, `mark_signal_sent`, etc. |
| `core/metrics.py` | Aggregates for API/dashboard. |
| `core/health_check.py` | Registers agent instances for monitoring hooks. |
| `core/websocket_manager.py` | Binance stream helpers / reconnect. |
| `core/utils.py` | `retry` decorator, `validate_price`, `is_stable_coin`, etc. |
| `core/analytics.py` | Helpers consumed by the enhanced dashboard. |
| `core/logger.py` | Rotating file + console loggers. |
| `core/rate_limiter.py` | e.g. DexScreener pacing. |

---

## 4. Technology stack

- **Runtime:** Python **3.10+**, **asyncio**
- **I/O:** **websockets**, **aiohttp**
- **Data:** **pandas**, **numpy** (where used in pipeline)
- **Messaging:** **python-telegram-bot** 20.x
- **Web:** **FastAPI**, **uvicorn**
- **Storage:** **sqlite3** (stdlib)
- **Config:** **python-dotenv** (optional env loading); primary config is **`config.py`** dataclasses

**Tests:** **pytest** (`tests/test_smoke.py` — imports, DB round-trip, config example exec, aggregator reason helper).

---

## 5. Configuration (engineering)

1. Copy `config.py.example` → `config.py` **or** export env vars.
2. Required for production Telegram path: **`TELEGRAM_BOT_TOKEN`**, **`TELEGRAM_CHAT_ID`**.
3. Tunables live on `Config` / nested dataclasses: symbol list, `min_confidence`, aggregation interval, volume/price thresholds, Binance WS URL, DexScreener timeouts, stable-coin filter set, log level and directory.

`config.validate()` runs at startup in `main.py`; fix reported errors before relying on Telegram.

---

## 6. Running locally

```bash
pip install -r requirements.txt
pytest
```

Core pipeline:

```bash
python main.py
```

Separate processes (typical dev):

```bash
python web/api.py      # default :8001, docs at /docs
python web/dashboard_enhanced.py   # default :8000
```

Windows: **`START.bat`** spawns API + dashboard in new consoles, then `main.py`.

---

## 7. Extending the codebase

- **New signal source:** implement async loop + `EventRouter.add_signal(Signal(...))`; register in `main.py` and `HealthCheck` if needed.
- **New storage:** extend `Database` schema + callers; keep writes async-friendly with the existing lock pattern.
- **New channel:** wrap `Signal` formatting similarly to `TelegramBot`; plug into `EventRouter(..., telegram_handler=...)` or handle outside the router.
- **ML / “learning”:** **not in-repo.** The stack is rule-based. External ML services can consume **`/api`** exports or read SQLite.

---

## 8. Utility scripts

| Script | Use |
|--------|-----|
| `check_system_status.py` | Deps, config presence, DB file, logs dir, localhost ports 8000/8001. |
| `check_agents.py` / `check_signals.py` | SQLite forensics for ops. |
| `test_run.py` | Long run with mock Telegram (writes `test_crypto_analytics.db`). |
| `get_chat_id.py` / `setup_telegram.py` | Chat ID discovery; **require `TELEGRAM_BOT_TOKEN` in environment** (no hardcoded secrets). |

---

## 9. Repository layout

```
agents/       # All agents
bot/          # Telegram, Discord, Email helpers
core/         # DB, routing, metrics, health, WS, analytics, logging, rate limits
web/          # FastAPI API + enhanced dashboard
tests/        # Pytest
main.py       # Orchestration
config.py     # Local (gitignored)
```

---

## 10. Disclaimer

Provided as-is. **Not financial advice.** No warranty of signal accuracy or availability of third-party APIs.
