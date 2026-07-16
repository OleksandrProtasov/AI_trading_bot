"""Monitor open paper trades and notify Telegram on SL/TP/partial."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.logger import get_logger
from core.partial_exit import (
    PartialHit,
    blend_position_pnl,
    check_live_hit,
    net_leg_return_pct,
    leg_return_pct,
    resolve_tp1,
    simulate_partial_path,
)

if TYPE_CHECKING:
    from bot.telegram_bot import TelegramBot
    from core.database import Database
    from config import Config


class PaperTradeMonitor:
    def __init__(self, db: "Database", config: "Config", telegram: Optional["TelegramBot"] = None):
        self.db = db
        self.config = config
        self.telegram = telegram
        self.running = True
        self.logger = get_logger(__name__)

    def _interval_sec(self) -> int:
        return int(getattr(self.config.agent, "paper_monitor_interval_sec", 30) or 30)

    def _fee_bps(self) -> float:
        return float(getattr(self.config.agent, "ev_fee_bps_per_side", 2.0))

    def _max_hold_sec(self) -> int:
        hours = float(getattr(self.config.agent, "paper_max_hold_hours", 24.0))
        return int(hours * 3600)

    def _partial_enabled(self) -> bool:
        return bool(getattr(self.config.agent, "paper_partial_enabled", True))

    def _partial_size(self) -> float:
        return float(getattr(self.config.agent, "paper_partial_size", 0.5))

    def _partial_rr(self) -> float:
        return float(getattr(self.config.agent, "paper_partial_rr", 1.5))

    def _resolve_tp1(self, trade: Dict[str, Any]) -> float:
        a = self.config.agent
        return resolve_tp1(
            float(trade["entry"]),
            float(trade["sl"]),
            float(trade["tp"]),
            str(trade["action"]),
            partial_enabled=self._partial_enabled(),
            partial_rr=self._partial_rr(),
            adaptive=bool(getattr(a, "paper_partial_adaptive", True)),
            min_tp_pct=float(getattr(a, "paper_partial_min_tp_pct", 3.5)),
            min_rr=float(getattr(a, "paper_partial_min_rr", 2.8)),
        )

    def _candle_since_ts(self, opened_at: int) -> int:
        return (int(opened_at) // 60) * 60

    async def _fetch_live_price(self, symbol: str) -> Optional[float]:
        import aiohttp

        url = "https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": symbol.upper()}
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    price = float(data.get("price") or 0)
                    return price if price > 0 else None
        except Exception as exc:
            self.logger.debug("Live price fetch %s: %s", symbol, exc)
            return None

    async def _load_candles_since(self, symbol: str, since_ts: int) -> List[Dict[str, Any]]:
        import sqlite3

        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM candles
                WHERE symbol=? AND timeframe='1m' AND timestamp >= ?
                ORDER BY timestamp ASC
                """,
                (symbol.upper(), int(since_ts)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    async def _notify_partial(self, trade: Dict[str, Any], hit: PartialHit) -> None:
        if not self.telegram:
            return
        from bot.telegram_format import format_paper_partial_alert

        text = format_paper_partial_alert(
            symbol=trade["symbol"],
            action=trade["action"],
            entry=float(trade["entry"]),
            tp1=float(hit.exit_price),
            partial_pnl_pct=float(hit.net_leg_pnl_pct),
            tp=float(trade["tp"]),
            partial_size=self._partial_size(),
        )
        try:
            await self.telegram.bot.send_message(
                chat_id=self.telegram.chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            self.logger.info(
                "Paper partial TP: %s %s +%.2f%% (%.0f%% position)",
                trade["symbol"],
                trade["action"],
                hit.net_leg_pnl_pct,
                self._partial_size() * 100,
            )
        except Exception as exc:
            self.logger.error("Paper partial telegram failed: %s", exc, exc_info=True)

    async def _notify_close(
        self,
        trade: Dict[str, Any],
        hit: PartialHit,
        *,
        blended_pnl: float,
    ) -> None:
        if not self.telegram:
            return
        from bot.telegram_format import format_paper_outcome_alert

        text = format_paper_outcome_alert(
            symbol=trade["symbol"],
            action=trade["action"],
            exit_reason=hit.kind,
            entry=float(trade["entry"]),
            exit_price=float(hit.exit_price),
            pnl_pct=float(blended_pnl),
            sl=float(trade.get("sl_after_partial") or trade["sl"]),
            tp=float(trade["tp"]),
            had_partial=bool(trade.get("partial_taken")),
        )
        try:
            await self.telegram.bot.send_message(
                chat_id=self.telegram.chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await self.db.mark_paper_trade_notified(int(trade["id"]))
            self.logger.info(
                "Paper %s closed: %s %s pnl=%+.2f%%",
                hit.kind,
                trade["symbol"],
                trade["action"],
                blended_pnl,
            )
        except Exception as exc:
            self.logger.error("Paper outcome telegram failed: %s", exc, exc_info=True)

    async def _apply_partial(self, trade: Dict[str, Any], hit: PartialHit) -> Dict[str, Any]:
        entry = float(trade["entry"])
        remain = max(0.0, 1.0 - self._partial_size())
        sl_be = entry
        if bool(getattr(self.config.agent, "paper_be_after_partial", True)):
            act = (trade.get("action") or "").upper()
            pad = 0.02
            if act == "BUY":
                sl_be = entry * (1.0 + pad / 100.0)
            elif act == "SELL":
                sl_be = entry * (1.0 - pad / 100.0)
        await self.db.record_partial_paper_trade(
            int(trade["id"]),
            partial_pnl_pct=float(hit.net_leg_pnl_pct),
            position_pct=remain,
            sl_after_partial=float(sl_be),
        )
        await self._notify_partial(trade, hit)
        trade = dict(trade)
        trade["partial_taken"] = 1
        trade["partial_pnl_pct"] = hit.net_leg_pnl_pct
        trade["position_pct"] = remain
        trade["sl_after_partial"] = sl_be
        return trade

    def _final_blended_pnl(self, trade: Dict[str, Any], hit: PartialHit) -> float:
        if int(trade.get("partial_taken") or 0):
            partial_pnl = float(trade.get("partial_pnl_pct") or 0.0)
            return blend_position_pnl(
                partial_pnl,
                hit.net_leg_pnl_pct,
                partial_size=self._partial_size(),
            )
        return hit.net_leg_pnl_pct

    async def _close_trade(
        self, trade: Dict[str, Any], hit: PartialHit, *, blended_pnl: float
    ) -> None:
        reason = hit.kind
        if reason == "be":
            reason = "sl"
        await self.db.close_paper_trade(
            int(trade["id"]),
            exit_reason=reason,
            exit_price=float(hit.exit_price),
            pnl_pct=float(blended_pnl),
        )
        if not trade.get("notified_close"):
            await self._notify_close(trade, hit, blended_pnl=blended_pnl)

    async def _check_trade(self, trade: Dict[str, Any]) -> None:
        tp1 = trade.get("tp1")
        if not tp1 or float(tp1 or 0) <= 0:
            trade = dict(trade)
            trade["tp1"] = self._resolve_tp1(trade)

        symbol = trade["symbol"]
        opened_at = int(trade["opened_at"])

        live_price = await self._fetch_live_price(symbol)
        if live_price is not None:
            live_hit = check_live_hit(trade, live_price, fee_bps_per_side=self._fee_bps())
            if live_hit:
                if live_hit.kind == "partial_tp":
                    await self._apply_partial(trade, live_hit)
                    return
                await self._close_trade(
                    trade,
                    live_hit,
                    blended_pnl=self._final_blended_pnl(trade, live_hit),
                )
                return

        candles = await self._load_candles_since(symbol, self._candle_since_ts(opened_at))
        if not candles:
            return

        import time as _time

        hit = simulate_partial_path(trade, candles, fee_bps_per_side=self._fee_bps())
        if hit:
            if hit.kind == "partial_tp":
                await self._apply_partial(trade, hit)
                return
            await self._close_trade(
                trade,
                hit,
                blended_pnl=self._final_blended_pnl(trade, hit),
            )
            return

        now = int(_time.time())
        if (now - opened_at) >= self._max_hold_sec():
            last = candles[-1]
            exit_px = float(last["close"])
            net = net_leg_return_pct(
                float(trade["entry"]),
                exit_px,
                str(trade["action"]),
                fee_bps_per_side=self._fee_bps(),
            )
            timeout_hit = PartialHit(
                kind="timeout",
                exit_price=exit_px,
                leg_pnl_pct=leg_return_pct(float(trade["entry"]), exit_px, str(trade["action"])),
                net_leg_pnl_pct=net,
            )
            await self._close_trade(
                trade,
                timeout_hit,
                blended_pnl=self._final_blended_pnl(trade, timeout_hit),
            )

    async def run(self) -> None:
        if not getattr(self.config.agent, "paper_monitor_enabled", True):
            self.logger.info("Paper trade monitor disabled.")
            return
        partial = "adaptive" if getattr(self.config.agent, "paper_partial_adaptive", True) else "always"
        if not self._partial_enabled():
            partial = "off"
        self.logger.info(
            "Paper trade monitor running (interval=%ss, partial=%s @ %.1fR)",
            self._interval_sec(),
            partial,
            self._partial_rr(),
        )
        while self.running:
            try:
                open_trades = await self.db.get_open_paper_trades()
                for trade in open_trades:
                    await self._check_trade(trade)
            except Exception as exc:
                self.logger.error("Paper monitor pass failed: %s", exc, exc_info=True)
            await asyncio.sleep(self._interval_sec())
