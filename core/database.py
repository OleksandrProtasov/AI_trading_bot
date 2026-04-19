"""Async-friendly SQLite access for candles, signals, and auxiliary tables."""
import sqlite3
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Any
import json
from core.logger import get_logger


class Database:
    def __init__(self, db_path: str = "crypto_analytics.db"):
        self.db_path = db_path
        self.lock = asyncio.Lock()
        self.logger = get_logger(__name__)
        self._init_db()
    
    def _init_db(self):
        """Create tables and indexes if missing."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # candles
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                UNIQUE(symbol, timeframe, timestamp)
            )
        """)
        
        # signals
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                agent_type TEXT NOT NULL,
                symbol TEXT,
                signal_type TEXT NOT NULL,
                priority TEXT NOT NULL,
                message TEXT NOT NULL,
                data TEXT,
                sent_to_telegram INTEGER DEFAULT 0
            )
        """)
        
        # whale_transactions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS whale_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                chain TEXT,
                token TEXT,
                from_address TEXT,
                to_address TEXT,
                amount REAL,
                value_usd REAL,
                transaction_type TEXT,
                data TEXT
            )
        """)
        
        # anomalies
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                symbol TEXT,
                anomaly_type TEXT NOT NULL,
                description TEXT NOT NULL,
                severity TEXT NOT NULL,
                data TEXT
            )
        """)
        
        # liquidity_zones
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS liquidity_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                price_level REAL NOT NULL,
                liquidity_amount REAL NOT NULL,
                zone_type TEXT NOT NULL,
                data TEXT
            )
        """)
        
        # indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_time ON candles(symbol, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_whale_timestamp ON whale_transactions(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_timestamp ON anomalies(timestamp)")
        
        conn.commit()
        conn.close()
    
    async def save_candle(self, symbol: str, timeframe: str, timestamp: int, 
                         open: float, high: float, low: float, close: float, volume: float):
        """Insert or replace one candle row."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO candles 
                    (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, timeframe, timestamp, open, high, low, close, volume))
                conn.commit()
            except Exception as e:
                self.logger.error("save_candle failed: %s", e, exc_info=True)
            finally:
                conn.close()
    
    async def save_signal(self, agent_type: str, signal_type: str, priority: str,
                         message: str, symbol: Optional[str] = None, data: Optional[Dict] = None):
        """Insert signal; returns row id."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                timestamp = int(datetime.utcnow().timestamp())
                data_json = json.dumps(data) if data else None
                cursor.execute("""
                    INSERT INTO signals 
                    (timestamp, agent_type, symbol, signal_type, priority, message, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (timestamp, agent_type, symbol, signal_type, priority, message, data_json))
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                self.logger.error("save_signal failed: %s", e, exc_info=True)
                return None
            finally:
                conn.close()
    
    async def save_whale_transaction(self, chain: str, token: str, from_address: str,
                                    to_address: str, amount: float, value_usd: float,
                                    transaction_type: str, data: Optional[Dict] = None):
        """Insert whale transaction row."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                timestamp = int(datetime.utcnow().timestamp())
                data_json = json.dumps(data) if data else None
                cursor.execute("""
                    INSERT INTO whale_transactions 
                    (timestamp, chain, token, from_address, to_address, amount, value_usd, transaction_type, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (timestamp, chain, token, from_address, to_address, amount, value_usd, transaction_type, data_json))
                conn.commit()
            except Exception as e:
                self.logger.error("save_whale_transaction failed: %s", e, exc_info=True)
            finally:
                conn.close()
    
    async def save_anomaly(self, symbol: str, anomaly_type: str, description: str,
                          severity: str, data: Optional[Dict] = None):
        """Insert anomaly row."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                timestamp = int(datetime.utcnow().timestamp())
                data_json = json.dumps(data) if data else None
                cursor.execute("""
                    INSERT INTO anomalies 
                    (timestamp, symbol, anomaly_type, description, severity, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (timestamp, symbol, anomaly_type, description, severity, data_json))
                conn.commit()
            except Exception as e:
                self.logger.error("save_anomaly failed: %s", e, exc_info=True)
            finally:
                conn.close()
    
    async def save_liquidity_zone(self, symbol: str, price_level: float, 
                                 liquidity_amount: float, zone_type: str,
                                 data: Optional[Dict] = None):
        """Insert liquidity zone snapshot."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                timestamp = int(datetime.utcnow().timestamp())
                data_json = json.dumps(data) if data else None
                cursor.execute("""
                    INSERT INTO liquidity_zones 
                    (timestamp, symbol, price_level, liquidity_amount, zone_type, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (timestamp, symbol, price_level, liquidity_amount, zone_type, data_json))
                conn.commit()
            except Exception as e:
                self.logger.error("save_liquidity_zone failed: %s", e, exc_info=True)
            finally:
                conn.close()
    
    async def get_recent_candles(self, symbol: str, timeframe: str, limit: int = 100) -> List[Dict]:
        """Return recent candles for symbol/timeframe."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT * FROM candles 
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (symbol, timeframe, limit))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            except Exception as e:
                self.logger.error("get_candles failed: %s", e, exc_info=True)
                return []
            finally:
                conn.close()
    
    async def mark_signal_sent(self, signal_id: int):
        """Mark signal.sent_to_telegram = 1."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE signals SET sent_to_telegram = 1 WHERE id = ?", (signal_id,))
                conn.commit()
            except Exception as e:
                self.logger.error("mark_signal_sent failed: %s", e, exc_info=True)
            finally:
                conn.close()

