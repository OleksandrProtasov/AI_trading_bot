"""
websocket_manager.py - улучшенное управление WebSocket соединениями
"""
import asyncio
import websockets
from typing import Optional, Callable, Any
from datetime import datetime
from core.logger import get_logger
from config import config


class WebSocketManager:
    """Менеджер для управления WebSocket соединениями с улучшенной логикой переподключения"""
    
    def __init__(self, url: str, ping_interval: int = None, ping_timeout: int = None):
        self.url = url
        self.ping_interval = ping_interval or config.binance.ping_interval
        self.ping_timeout = ping_timeout or config.binance.ping_timeout
        self.logger = get_logger(__name__)
        self.ws: Optional[websockets.WebSocketServerProtocol] = None
        self.running = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.base_delay = config.binance.reconnect_delay
        self.last_connect_time = None
    
    async def connect(self):
        """Подключение с экспоненциальной задержкой"""
        while self.running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                self.ws = await websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    close_timeout=10
                )
                self.last_connect_time = datetime.utcnow()
                self.reconnect_attempts = 0
                self.logger.info(f"WebSocket подключен: {self.url}")
                return self.ws
            except Exception as e:
                self.reconnect_attempts += 1
                delay = min(self.base_delay * (2 ** (self.reconnect_attempts - 1)), 60)  # Макс 60 сек
                self.logger.warning(
                    f"Ошибка подключения (попытка {self.reconnect_attempts}/{self.max_reconnect_attempts}): {e}. "
                    f"Повтор через {delay:.1f}с..."
                )
                await asyncio.sleep(delay)
        
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.error(f"Достигнут максимум попыток переподключения для {self.url}")
        return None
    
    async def reconnect(self):
        """Переподключение"""
        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
        self.ws = None
        return await self.connect()
    
    async def send(self, message: Any):
        """Отправка сообщения"""
        if not self.ws:
            await self.connect()
        if self.ws:
            try:
                await self.ws.send(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("Соединение закрыто при отправке, переподключение...")
                await self.reconnect()
                if self.ws:
                    await self.ws.send(message)
            except Exception as e:
                self.logger.error(f"Ошибка отправки: {e}", exc_info=True)
                raise
    
    async def recv(self):
        """Получение сообщения"""
        if not self.ws:
            await self.connect()
        if self.ws:
            try:
                return await self.ws.recv()
            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("Соединение закрыто при получении, переподключение...")
                await self.reconnect()
                if self.ws:
                    return await self.ws.recv()
            except Exception as e:
                self.logger.error(f"Ошибка получения: {e}", exc_info=True)
                raise
        return None
    
    async def close(self):
        """Корректное закрытие соединения"""
        self.running = False
        if self.ws:
            try:
                await self.ws.close()
                self.logger.info("WebSocket соединение закрыто")
            except Exception as e:
                self.logger.debug(f"Ошибка при закрытии: {e}")
            finally:
                self.ws = None
    
    async def listen(self, message_handler: Callable, running_flag: Callable = None):
        """Прослушивание сообщений с автоматическим переподключением"""
        self.running = True
        await self.connect()
        
        while self.running:
            if running_flag and not running_flag():
                break
                
            try:
                if not self.ws:
                    await self.reconnect()
                    if not self.ws:
                        await asyncio.sleep(self.base_delay)
                        continue
                
                async for message in self.ws:
                    if not self.running or (running_flag and not running_flag()):
                        break
                    try:
                        await message_handler(message)
                    except Exception as e:
                        self.logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)
                        
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning(f"Соединение закрыто: {e}, переподключение...")
                await self.reconnect()
                if not self.ws:
                    await asyncio.sleep(self.base_delay)
            except Exception as e:
                self.logger.error(f"Ошибка в listen: {e}", exc_info=True)
                await asyncio.sleep(self.base_delay)
                await self.reconnect()
        
        await self.close()

