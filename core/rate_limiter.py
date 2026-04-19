"""
rate_limiter.py - ограничение частоты запросов к API
"""
import asyncio
import time
from collections import deque
from typing import Optional
from core.logger import get_logger


class RateLimiter:
    """Rate limiter для API запросов"""
    
    def __init__(self, max_calls: int, time_window: float):
        """
        Args:
            max_calls: Максимальное количество вызовов
            time_window: Временное окно в секундах
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self.lock = asyncio.Lock()
        self.logger = get_logger(__name__)
    
    async def acquire(self):
        """Получение разрешения на вызов"""
        async with self.lock:
            now = time.time()
            
            # Удаляем старые вызовы
            while self.calls and self.calls[0] < now - self.time_window:
                self.calls.popleft()
            
            # Проверяем лимит
            if len(self.calls) >= self.max_calls:
                # Ждем до освобождения слота
                wait_time = self.calls[0] + self.time_window - now
                if wait_time > 0:
                    self.logger.debug(f"Rate limit достигнут, ожидание {wait_time:.2f}с")
                    await asyncio.sleep(wait_time)
                    # Повторно очищаем старые вызовы
                    while self.calls and self.calls[0] < time.time() - self.time_window:
                        self.calls.popleft()
            
            # Регистрируем вызов
            self.calls.append(time.time())
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# Глобальные rate limiters для разных API
dex_screener_limiter = RateLimiter(max_calls=10, time_window=1.0)  # 10 запросов в секунду
binance_limiter = RateLimiter(max_calls=1200, time_window=60.0)  # 1200 запросов в минуту




