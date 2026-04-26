"""Periodic evaluation of aggregated signal outcomes vs candle path."""
from __future__ import annotations

import asyncio
from calendar import timegm
from datetime import datetime
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.database import Database
    from config import Config


class OutcomeEvaluationService:
    """Background loop: mark `aggregated_outcomes` rows when horizon elapses."""

    def __init__(self, db: "Database", config: "Config"):
        self.db = db
        self.config = config
        self.running = True
        self.logger = get_logger(__name__)

    def _interval_sec(self) -> int:
        return int(
            getattr(self.config.agent, "outcome_eval_interval_sec", 300) or 300
        )

    def _threshold_pct(self) -> float:
        return float(
            getattr(self.config.agent, "outcome_direction_threshold_pct", 0.05)
        )

    async def run(self) -> None:
        if not getattr(self.config.agent, "outcome_tracking_enabled", True):
            self.logger.info("Outcome tracking disabled; evaluator not started.")
            return

        self.logger.info(
            "Outcome evaluator running (interval=%ss)", self._interval_sec()
        )
        while self.running:
            try:
                now_ts = timegm(datetime.utcnow().utctimetuple())
                n = await self.db.evaluate_pending_aggregated_outcomes(
                    now_ts,
                    direction_threshold_pct=self._threshold_pct(),
                )
                if n:
                    self.logger.info("Evaluated %s aggregated outcome row(s)", n)
            except Exception as e:
                self.logger.error("Outcome evaluation pass failed: %s", e, exc_info=True)
            await asyncio.sleep(self._interval_sec())
