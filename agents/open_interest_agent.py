"""Poll Binance futures open interest and cache in SQLite."""
from __future__ import annotations

import asyncio
import time
from typing import List, Optional, Set

from config import config
from core.database import Database
from core.event_router import EventRouter
from core.logger import get_logger
from core.open_interest import fetch_futures_usdt_symbols, fetch_open_interest


class OpenInterestAgent:
    def __init__(
        self,
        db: Database,
        event_router: EventRouter,
        symbols: List[str],
    ):
        self.db = db
        self.event_router = event_router
        self.symbols = [s.upper() for s in symbols]
        self.running = False
        self.logger = get_logger(__name__)
        self._futures_symbols: Set[str] = set()

    def _interval_sec(self) -> int:
        return int(getattr(config.agent, "oi_poll_interval_sec", 300) or 300)

    def _enabled(self) -> bool:
        return bool(getattr(config.agent, "oi_enabled", True))

    async def start(self) -> None:
        if not self._enabled():
            self.logger.info("Open interest agent disabled.")
            return
        self.running = True
        self.logger.info(
            "Open interest agent running (interval=%ss, symbols=%s)",
            self._interval_sec(),
            len(self.symbols),
        )
        if self.event_router:
            self.event_router.ping_health("open_interest")
        while self.running:
            try:
                await self._poll_once()
            except Exception as exc:
                self.logger.error("OI poll failed: %s", exc, exc_info=True)
            if self.event_router:
                self.event_router.ping_health("open_interest")
            await asyncio.sleep(self._interval_sec())

    async def _poll_once(self) -> None:
        if not self._futures_symbols:
            loop = asyncio.get_event_loop()
            self._futures_symbols = await loop.run_in_executor(
                None, fetch_futures_usdt_symbols
            )
            self.logger.info("Futures symbols loaded: %s", len(self._futures_symbols))

        sem = asyncio.Semaphore(8)
        ts = int(time.time())

        async def _one(sym: str) -> Optional[dict]:
            if sym not in self._futures_symbols:
                return None
            async with sem:
                loop = asyncio.get_event_loop()
                oi = await loop.run_in_executor(None, fetch_open_interest, sym)
            if oi is None:
                return None
            return {"symbol": sym, "open_interest": oi}

        tasks = [_one(s) for s in self.symbols]
        results = await asyncio.gather(*tasks)
        rows = [r for r in results if r]
        n = await self.db.save_oi_snapshots(rows, timestamp=ts)
        if n:
            self.logger.debug("OI snapshots saved: %s", n)
        if self.event_router:
            self.event_router.ping_health("open_interest")

    async def stop(self) -> None:
        self.running = False
