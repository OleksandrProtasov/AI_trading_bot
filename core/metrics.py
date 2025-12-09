"""
metrics.py - метрики и статистика системы
"""
import sqlite3
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from core.database import Database


class Metrics:
    """Класс для сбора и анализа метрик"""
    
    def __init__(self, db: Database):
        self.db = db
        self.signal_stats = defaultdict(int)  # {agent_type: count}
        self.signal_types = defaultdict(int)  # {signal_type: count}
        self.symbol_stats = defaultdict(int)  # {symbol: count}
        self.error_count = 0
        self.start_time = datetime.utcnow()
    
    def record_signal(self, agent_type: str, signal_type: str, symbol: Optional[str] = None):
        """Запись сигнала в метрики"""
        self.signal_stats[agent_type] += 1
        self.signal_types[signal_type] += 1
        if symbol:
            self.symbol_stats[symbol] += 1
    
    def record_error(self):
        """Запись ошибки"""
        self.error_count += 1
    
    async def get_statistics(self, hours: int = 24) -> Dict:
        """Получение статистики за период"""
        try:
            since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
            
            # Подключаемся к БД
            conn = sqlite3.connect(self.db.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            stats = {
                'period_hours': hours,
                'total_signals': 0,
                'by_agent': {},
                'by_type': {},
                'by_symbol': {},
                'sent_to_telegram': 0,
                'errors': self.error_count,
                'uptime_hours': (datetime.utcnow() - self.start_time).total_seconds() / 3600
            }
            
            # Всего сигналов
            cursor.execute("SELECT COUNT(*) as total FROM signals WHERE timestamp > ?", (since,))
            stats['total_signals'] = cursor.fetchone()['total']
            
            # По агентам
            cursor.execute("""
                SELECT agent_type, COUNT(*) as count 
                FROM signals 
                WHERE timestamp > ?
                GROUP BY agent_type
            """, (since,))
            for row in cursor.fetchall():
                stats['by_agent'][row['agent_type']] = row['count']
            
            # По типам
            cursor.execute("""
                SELECT signal_type, COUNT(*) as count 
                FROM signals 
                WHERE timestamp > ?
                GROUP BY signal_type
            """, (since,))
            for row in cursor.fetchall():
                stats['by_type'][row['signal_type']] = row['count']
            
            # По символам
            cursor.execute("""
                SELECT symbol, COUNT(*) as count 
                FROM signals 
                WHERE timestamp > ? AND symbol IS NOT NULL
                GROUP BY symbol
                ORDER BY count DESC
                LIMIT 10
            """, (since,))
            for row in cursor.fetchall():
                stats['by_symbol'][row['symbol']] = row['count']
            
            # Отправлено в Telegram
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM signals 
                WHERE timestamp > ? AND sent_to_telegram = 1
            """, (since,))
            stats['sent_to_telegram'] = cursor.fetchone()['count']
            
            conn.close()
            return stats
            
        except Exception as e:
            # Используем базовый print, так как logger может быть недоступен
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка получения статистики: {e}", exc_info=True)
            return {}
    
    def get_summary(self) -> str:
        """Получение краткой сводки"""
        uptime = (datetime.utcnow() - self.start_time).total_seconds() / 3600
        return (
            f"Uptime: {uptime:.1f}h | "
            f"Signals: {sum(self.signal_stats.values())} | "
            f"Errors: {self.error_count}"
        )

