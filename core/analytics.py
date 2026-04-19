"""
analytics.py - расширенная аналитика и backtesting
"""
import sqlite3
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from core.database import Database
from core.logger import get_logger


class Analytics:
    """Класс для аналитики и backtesting"""
    
    def __init__(self, db: Database):
        self.db = db
        self.logger = get_logger(__name__)
    
    async def analyze_signal_performance(self, hours: int = 24) -> Dict:
        """Анализ эффективности сигналов"""
        try:
            conn = sqlite3.connect(self.db.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
            
            # Получаем все сигналы
            cursor.execute("""
                SELECT symbol, signal_type, timestamp, data
                FROM signals
                WHERE timestamp > ? AND symbol IS NOT NULL
                ORDER BY timestamp
            """, (since,))
            
            signals = cursor.fetchall()
            
            # Группируем по символам
            symbol_signals = defaultdict(list)
            for signal in signals:
                symbol_signals[signal['symbol']].append(signal)
            
            # Анализируем каждый символ
            results = {
                'total_signals': len(signals),
                'symbols_analyzed': len(symbol_signals),
                'by_agent': defaultdict(int),
                'by_type': defaultdict(int),
                'performance': {}
            }
            
            for symbol, sigs in symbol_signals.items():
                # Подсчитываем типы сигналов
                for sig in sigs:
                    results['by_agent'][sig['agent_type']] += 1
                    results['by_type'][sig['signal_type']] += 1
                
                # Простая метрика: количество сигналов на символ
                results['performance'][symbol] = {
                    'signals_count': len(sigs),
                    'unique_types': len(set(s['signal_type'] for s in sigs)),
                    'first_signal': min(s['timestamp'] for s in sigs),
                    'last_signal': max(s['timestamp'] for s in sigs)
                }
            
            conn.close()
            return results
        except Exception as e:
            self.logger.error(f"Ошибка анализа эффективности: {e}", exc_info=True)
            return {}
    
    async def backtest_strategy(self, symbol: str, start_time: int, end_time: int,
                               strategy: str = "buy_and_hold") -> Dict:
        """Простой backtesting стратегии"""
        try:
            conn = sqlite3.connect(self.db.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Получаем свечи за период
            cursor.execute("""
                SELECT * FROM candles
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
            """, (symbol, start_time, end_time))
            
            candles = cursor.fetchall()
            
            if len(candles) < 2:
                return {'error': 'Недостаточно данных'}
            
            # Получаем сигналы за период
            cursor.execute("""
                SELECT * FROM signals
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
            """, (symbol, start_time, end_time))
            
            signals = cursor.fetchall()
            
            # Простой backtest: покупаем на первом сигнале BUY, продаем на SELL
            initial_price = candles[0]['close']
            final_price = candles[-1]['close']
            
            buy_signals = [s for s in signals if 'buy' in s['signal_type'].lower()]
            sell_signals = [s for s in signals if 'sell' in s['signal_type'].lower() or 'exit' in s['signal_type'].lower()]
            
            # Расчет прибыли
            if strategy == "buy_and_hold":
                profit_pct = ((final_price - initial_price) / initial_price) * 100
            else:
                # Стратегия на основе сигналов
                profit_pct = 0.0
                # Упрощенная версия - можно расширить
            
            conn.close()
            
            return {
                'symbol': symbol,
                'period': {
                    'start': start_time,
                    'end': end_time,
                    'days': (end_time - start_time) / 86400
                },
                'initial_price': initial_price,
                'final_price': final_price,
                'profit_pct': profit_pct,
                'signals': {
                    'total': len(signals),
                    'buy': len(buy_signals),
                    'sell': len(sell_signals)
                },
                'candles_count': len(candles)
            }
        except Exception as e:
            self.logger.error(f"Ошибка backtesting: {e}", exc_info=True)
            return {'error': str(e)}
    
    async def get_correlation(self, symbol1: str, symbol2: str, hours: int = 24) -> Dict:
        """Корреляция между двумя символами"""
        try:
            conn = sqlite3.connect(self.db.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
            
            # Получаем свечи для обоих символов
            cursor.execute("""
                SELECT timestamp, close FROM candles
                WHERE symbol = ? AND timestamp > ?
                ORDER BY timestamp
            """, (symbol1, since))
            candles1 = {row['timestamp']: row['close'] for row in cursor.fetchall()}
            
            cursor.execute("""
                SELECT timestamp, close FROM candles
                WHERE symbol = ? AND timestamp > ?
                ORDER BY timestamp
            """, (symbol2, since))
            candles2 = {row['timestamp']: row['close'] for row in cursor.fetchall()}
            
            # Находим общие временные точки
            common_timestamps = set(candles1.keys()) & set(candles2.keys())
            
            if len(common_timestamps) < 2:
                return {'error': 'Недостаточно общих данных'}
            
            # Вычисляем корреляцию (упрощенная версия)
            prices1 = [candles1[t] for t in sorted(common_timestamps)]
            prices2 = [candles2[t] for t in sorted(common_timestamps)]
            
            # Нормализуем цены (процентное изменение)
            changes1 = [(prices1[i] - prices1[0]) / prices1[0] for i in range(1, len(prices1))]
            changes2 = [(prices2[i] - prices2[0]) / prices2[0] for i in range(1, len(prices2))]
            
            # Простая корреляция
            if len(changes1) != len(changes2):
                min_len = min(len(changes1), len(changes2))
                changes1 = changes1[:min_len]
                changes2 = changes2[:min_len]
            
            # Вычисляем коэффициент корреляции Пирсона (упрощенно)
            mean1 = sum(changes1) / len(changes1) if changes1 else 0
            mean2 = sum(changes2) / len(changes2) if changes2 else 0
            
            numerator = sum((changes1[i] - mean1) * (changes2[i] - mean2) for i in range(len(changes1)))
            denom1 = sum((x - mean1) ** 2 for x in changes1) ** 0.5
            denom2 = sum((x - mean2) ** 2 for x in changes2) ** 0.5
            
            correlation = (numerator / (denom1 * denom2)) if (denom1 * denom2) > 0 else 0
            
            conn.close()
            
            return {
                'symbol1': symbol1,
                'symbol2': symbol2,
                'correlation': correlation,
                'data_points': len(common_timestamps),
                'interpretation': self._interpret_correlation(correlation)
            }
        except Exception as e:
            self.logger.error(f"Ошибка вычисления корреляции: {e}", exc_info=True)
            return {'error': str(e)}
    
    def _interpret_correlation(self, corr: float) -> str:
        """Интерпретация корреляции"""
        if abs(corr) > 0.7:
            return "Сильная корреляция"
        elif abs(corr) > 0.4:
            return "Умеренная корреляция"
        elif abs(corr) > 0.2:
            return "Слабая корреляция"
        else:
            return "Нет значимой корреляции"




