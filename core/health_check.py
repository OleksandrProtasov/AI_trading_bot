"""Lightweight agent liveness tracking (best-effort, not a full APM)."""
import asyncio
from typing import Dict, Optional
from datetime import datetime, timedelta
from enum import Enum
from core.logger import get_logger


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthCheck:
    def __init__(self):
        self.logger = get_logger(__name__)
        self.agents_status: Dict[str, Dict] = {}
        self.last_check = {}
        self.check_interval = 30
        self.max_silence_time = 120

    def register_agent(self, agent_name: str, agent_instance):
        now = datetime.utcnow()
        self.agents_status[agent_name] = {
            "instance": agent_instance,
            "status": HealthStatus.HEALTHY,
            "last_activity": now,
            "last_signal_time": None,
            "error_count": 0,
            "last_error": None,
        }
        self.logger.info("HealthCheck registered agent: %s", agent_name)
    
    def update_activity(self, agent_name: str):
        if agent_name in self.agents_status:
            self.agents_status[agent_name]["last_activity"] = datetime.utcnow()
            self.agents_status[agent_name]["status"] = HealthStatus.HEALTHY

    def update_signal(self, agent_name: str):
        if agent_name in self.agents_status:
            self.agents_status[agent_name]["last_signal_time"] = datetime.utcnow()
            self.update_activity(agent_name)

    def record_error(self, agent_name: str, error: Exception):
        if agent_name in self.agents_status:
            self.agents_status[agent_name]["error_count"] += 1
            self.agents_status[agent_name]["last_error"] = {
                "message": str(error),
                "time": datetime.utcnow(),
            }
            if self.agents_status[agent_name]["error_count"] > 10:
                self.agents_status[agent_name]["status"] = HealthStatus.UNHEALTHY

    async def check_health(self) -> Dict[str, HealthStatus]:
        results = {}
        now = datetime.utcnow()

        for agent_name, info in self.agents_status.items():
            status = HealthStatus.UNKNOWN

            if hasattr(info["instance"], "running"):
                if not info["instance"].running:
                    # Still starting (gather may not have entered agent.start() yet)
                    status = HealthStatus.UNKNOWN
                else:
                    last = info["last_activity"]
                    if last is None:
                        status = HealthStatus.HEALTHY
                    else:
                        silence_time = (now - last).total_seconds()
                        if silence_time > self.max_silence_time:
                            status = HealthStatus.DEGRADED
                        else:
                            status = HealthStatus.HEALTHY

            if info["error_count"] > 5:
                status = HealthStatus.DEGRADED
            if info["error_count"] > 10:
                status = HealthStatus.UNHEALTHY

            info["status"] = status
            results[agent_name] = status
            self.last_check[agent_name] = now

        return results

    async def monitor(self):
        while True:
            try:
                await self.check_health()

                for agent_name, info in self.agents_status.items():
                    if info["status"] in (
                        HealthStatus.DEGRADED,
                        HealthStatus.UNHEALTHY,
                    ):
                        self.logger.warning(
                            "Agent %s: %s (errors=%s last_activity=%s)",
                            agent_name,
                            info["status"].value,
                            info["error_count"],
                            info["last_activity"],
                        )

                await asyncio.sleep(self.check_interval)
            except Exception as e:
                self.logger.error("Health monitor error: %s", e, exc_info=True)
                await asyncio.sleep(self.check_interval)

    def get_status_summary(self) -> str:
        summary = []
        for agent_name, info in self.agents_status.items():
            status_emoji = {
                HealthStatus.HEALTHY: "✅",
                HealthStatus.DEGRADED: "⚠️",
                HealthStatus.UNHEALTHY: "❌",
                HealthStatus.UNKNOWN: "❓",
            }
            emoji = status_emoji.get(info["status"], "❓")
            summary.append(f"{emoji} {agent_name}: {info['status'].value}")
        return "\n".join(summary)




