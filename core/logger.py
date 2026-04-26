"""Rotating file + console logging helpers."""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(
    name: str,
    log_dir: str = "logs",
    level: int | None = None,
) -> logging.Logger:
    """
    Настройка логгера с файловым и консольным выводом
    
    Args:
        name: Имя логгера (обычно __name__)
        log_dir: Директория для логов
        level: Уровень логирования
    
    Returns:
        Настроенный логгер
    """
    if level is None:
        level = getattr(
            logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO
        )

    # Создаем директорию для логов
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Избегаем дублирования handlers
    if logger.handlers:
        return logger
    
    # Формат логов
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_formatter = logging.Formatter(
        '%(levelname)s - [%(name)s] - %(message)s'
    )
    
    # Файловый handler с ротацией (10MB, 5 файлов)
    log_file = os.path.join(log_dir, f"{name.replace('.', '_')}.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(file_formatter)
    
    # Консоль: по умолчанию тот же уровень, что и файл (INFO — видно, что происходит).
    # Узко: CONSOLE_LOG_LEVEL=WARNING
    _cl = os.getenv("CONSOLE_LOG_LEVEL", "").strip().upper()
    console_level = (
        getattr(logging, _cl, level) if _cl else level
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Получить логгер (создает если не существует)"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger




