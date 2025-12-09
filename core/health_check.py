"""
health_check.py - система проверки здоровья агентов
"""
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
    """Система мониторинга здоровья компонентов"""
    
    def __init__(self):
        self.logger = get_logger(__name__)
        self.agents_status: Dict[str, Dict] = {}
        self.last_check = {}
        self.check_interval = 30  # Проверка каждые 30 секунд
        self.max_silence_time = 120  # Максимальное время молчания (секунды)
    
    def register_agent(self, agent_name: str, agent_instance):
        """Регистрация агента для мониторинга"""
        self.agents_status[agent_name] = {
            'instance': agent_instance,
            'status': HealthStatus.UNKNOWN,
            'last_activity': None,
            'last_signal_time': None,
            'error_count': 0,
            'last_error': None
        }
        self.logger.info(f"Зарегистрирован агент для мониторинга: {agent_name}")
    
    def update_activity(self, agent_name: str):
        """Обновление активности агента"""
        if agent_name in self.agents_status:
            self.agents_status[agent_name]['last_activity'] = datetime.utcnow()
            self.agents_status[agent_name]['status'] = HealthStatus.HEALTHY
    
    def update_signal(self, agent_name: str):
        """Обновление времени последнего сигнала"""
        if agent_name in self.agents_status:
            self.agents_status[agent_name]['last_signal_time'] = datetime.utcnow()
            self.update_activity(agent_name)
    
    def record_error(self, agent_name: str, error: Exception):
        """Запись ошибки агента"""
        if agent_name in self.agents_status:
            self.agents_status[agent_name]['error_count'] += 1
            self.agents_status[agent_name]['last_error'] = {
                'message': str(error),
                'time': datetime.utcnow()
            }
            # Если много ошибок - помечаем как unhealthy
            if self.agents_status[agent_name]['error_count'] > 10:
                self.agents_status[agent_name]['status'] = HealthStatus.UNHEALTHY
    
    async def check_health(self) -> Dict[str, HealthStatus]:
        """Проверка здоровья всех агентов"""
        results = {}
        now = datetime.utcnow()
        
        for agent_name, info in self.agents_status.items():
            status = HealthStatus.UNKNOWN
            
            # Проверяем, запущен ли агент
            if hasattr(info['instance'], 'running'):
                if not info['instance'].running:
                    status = HealthStatus.UNHEALTHY
                else:
                    # Проверяем последнюю активность
                    if info['last_activity']:
                        silence_time = (now - info['last_activity']).total_seconds()
                        if silence_time > self.max_silence_time:
                            status = HealthStatus.DEGRADED
                        else:
                            status = HealthStatus.HEALTHY
                    else:
                        status = HealthStatus.UNKNOWN
            
            # Учитываем количество ошибок
            if info['error_count'] > 5:
                status = HealthStatus.DEGRADED
            if info['error_count'] > 10:
                status = HealthStatus.UNHEALTHY
            
            info['status'] = status
            results[agent_name] = status
            self.last_check[agent_name] = now
        
        return results
    
    async def monitor(self):
        """Непрерывный мониторинг"""
        while True:
            try:
                await self.check_health()
                
                # Логируем статусы
                for agent_name, info in self.agents_status.items():
                    if info['status'] != HealthStatus.HEALTHY:
                        self.logger.warning(
                            f"Агент {agent_name}: {info['status'].value} "
                            f"(ошибок: {info['error_count']}, "
                            f"последняя активность: {info['last_activity']})"
                        )
                
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                self.logger.error(f"Ошибка мониторинга: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)
    
    def get_status_summary(self) -> str:
        """Получение сводки статусов"""
        summary = []
        for agent_name, info in self.agents_status.items():
            status_emoji = {
                HealthStatus.HEALTHY: "✅",
                HealthStatus.DEGRADED: "⚠️",
                HealthStatus.UNHEALTHY: "❌",
                HealthStatus.UNKNOWN: "❓"
            }
            emoji = status_emoji.get(info['status'], "❓")
            summary.append(f"{emoji} {agent_name}: {info['status'].value}")
        return "\n".join(summary)

