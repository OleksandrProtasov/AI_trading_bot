"""
email_notifier.py - уведомления по Email
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List
from core.logger import get_logger
from core.event_router import Signal
from agents.aggregator_agent import AggregatedSignal


class EmailNotifier:
    """Отправка уведомлений по Email"""
    
    def __init__(self, smtp_server: Optional[str] = None, smtp_port: int = 587,
                 username: Optional[str] = None, password: Optional[str] = None,
                 recipients: Optional[List[str]] = None):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.recipients = recipients or []
        self.logger = get_logger(__name__)
        self.enabled = all([smtp_server, username, password, recipients])
    
    async def send_signal(self, signal: Signal):
        """Отправка сигнала по Email"""
        if not self.enabled:
            return
        
        try:
            subject = f"[{signal.priority.value.upper()}] {signal.signal_type} - {signal.symbol or 'N/A'}"
            
            body = f"""
Сигнал от {signal.agent_type}

Тип: {signal.signal_type}
Приоритет: {signal.priority.value}
Символ: {signal.symbol or 'N/A'}
Время: {signal.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

Сообщение:
{signal.message}

---
Crypto Analytics System
            """.strip()
            
            await self._send_email(subject, body)
            self.logger.debug(f"Сигнал отправлен по Email: {signal.signal_type}")
        except Exception as e:
            self.logger.error(f"Ошибка отправки Email: {e}", exc_info=True)
    
    async def send_aggregated_signal(self, aggregated: AggregatedSignal):
        """Отправка агрегированного сигнала"""
        if not self.enabled:
            return
        
        try:
            subject = f"[{aggregated.action.value}] {aggregated.symbol} - Уверенность: {aggregated.confidence*100:.1f}%"
            
            reasons_text = "\n".join([f"  • {r}" for r in aggregated.reasons])
            
            body = f"""
АГРЕГИРОВАННЫЙ СИГНАЛ

Действие: {aggregated.action.value}
Символ: {aggregated.symbol}
Уверенность: {aggregated.confidence*100:.1f}%
Риск: {aggregated.risk.value}

Причины:
{reasons_text}

Ценовые уровни:
  Цена: {aggregated.price or 'N/A'}
  Entry: {aggregated.entry or 'N/A'}
  SL: {aggregated.sl or 'N/A'}
  TP: {aggregated.tp or 'N/A'}

Время: {aggregated.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

---
Crypto Analytics System
            """.strip()
            
            await self._send_email(subject, body)
            self.logger.debug(f"Агрегированный сигнал отправлен по Email: {aggregated.symbol} {aggregated.action.value}")
        except Exception as e:
            self.logger.error(f"Ошибка отправки агрегированного сигнала по Email: {e}", exc_info=True)
    
    async def _send_email(self, subject: str, body: str):
        """Внутренний метод отправки Email"""
        import asyncio
        
        def _send():
            msg = MIMEMultipart()
            msg['From'] = self.username
            msg['To'] = ", ".join(self.recipients)
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
        
        # Выполняем в executor, так как smtplib синхронный
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send)




