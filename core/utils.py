"""
utils.py - утилиты и вспомогательные функции
"""
import asyncio
import functools
from typing import Callable, TypeVar, Any
from datetime import datetime, timedelta

T = TypeVar('T')


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, 
          exceptions: tuple = (Exception,), logger=None):
    """
    Декоратор для повторных попыток с экспоненциальной задержкой
    
    Args:
        max_attempts: Максимум попыток
        delay: Начальная задержка (секунды)
        backoff: Множитель задержки
        exceptions: Кортеж исключений для перехвата
        logger: Логгер для записи ошибок
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts - 1:
                        if logger:
                            logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    
                    wait_time = delay * (backoff ** attempt)
                    if logger:
                        logger.warning(f"{func.__name__} attempt {attempt + 1}/{max_attempts} failed: {e}. Retrying in {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
            
            if last_exception:
                raise last_exception
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts - 1:
                        if logger:
                            logger.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    
                    wait_time = delay * (backoff ** attempt)
                    if logger:
                        logger.warning(f"{func.__name__} attempt {attempt + 1}/{max_attempts} failed: {e}. Retrying in {wait_time:.1f}s...")
                    import time
                    time.sleep(wait_time)
            
            if last_exception:
                raise last_exception
        
        # Определяем async или sync
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def validate_price(price: Any) -> float:
    """Валидация и преобразование цены"""
    if price is None:
        raise ValueError("Price cannot be None")
    
    try:
        price_float = float(price)
        if price_float <= 0:
            raise ValueError(f"Price must be positive, got {price_float}")
        if price_float > 1e10:  # Разумный максимум
            raise ValueError(f"Price too large: {price_float}")
        return price_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid price format: {price}") from e


def validate_symbol(symbol: Any) -> str:
    """Валидация символа"""
    if symbol is None:
        raise ValueError("Symbol cannot be None")
    
    symbol_str = str(symbol).strip().upper()
    
    if len(symbol_str) < 3:
        raise ValueError(f"Symbol too short: {symbol_str}")
    
    if len(symbol_str) > 20:
        raise ValueError(f"Symbol too long: {symbol_str}")
    
    # Проверка на валидные символы
    if not symbol_str.replace('USDT', '').replace('USDC', '').replace('BUSD', '').isalnum():
        raise ValueError(f"Symbol contains invalid characters: {symbol_str}")
    
    return symbol_str


def validate_volume(volume: Any) -> float:
    """Валидация объема"""
    if volume is None:
        raise ValueError("Volume cannot be None")
    
    try:
        volume_float = float(volume)
        if volume_float < 0:
            raise ValueError(f"Volume cannot be negative: {volume_float}")
        return volume_float
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid volume format: {volume}") from e


def is_stable_coin(symbol: str, stable_coins: set, price: float = None) -> bool:
    """
    Проверка, является ли символ стабильной монетой
    
    Args:
        symbol: Символ для проверки
        stable_coins: Множество стабильных монет
        price: Цена (если доступна, проверяем что ~$1)
    
    Returns:
        True если стабильная монета
    """
    symbol_upper = symbol.upper()
    
    # Прямая проверка
    if symbol_upper in stable_coins:
        return True
    
    # Проверка по цене (стабильные монеты ~$1)
    if price is not None:
        if 0.99 <= price <= 1.01:
            return True
    
    # Проверка по суффиксу (USDT, USDC и т.д.)
    if symbol_upper.endswith('USDT') and len(symbol_upper) == 4:
        return True
    
    return False


def format_number(value: float, decimals: int = 4) -> str:
    """Форматирование числа для вывода"""
    if value is None:
        return "N/A"
    
    try:
        if abs(value) >= 1e6:
            return f"{value/1e6:.2f}M"
        elif abs(value) >= 1e3:
            return f"{value/1e3:.2f}K"
        else:
            return f"{value:.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """Вычисление процентного изменения"""
    if old_value == 0:
        return 0.0
    return ((new_value - old_value) / old_value) * 100.0




