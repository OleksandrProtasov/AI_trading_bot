"""Async-friendly SQLite access for candles, signals, and auxiliary tables."""
import sqlite3
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Any

import json

from core.logger import get_logger
from core.outcome_math import compute_path_metrics


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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS aggregated_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_ts INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                baseline_action TEXT NOT NULL,
                council_enabled INTEGER NOT NULL DEFAULT 1,
                council_changed INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL,
                risk TEXT NOT NULL,
                price_at_signal REAL,
                reasons_json TEXT,
                horizon_sec INTEGER NOT NULL,
                sent_telegram INTEGER NOT NULL DEFAULT 0,
                evaluated_at INTEGER,
                close_at_horizon REAL,
                return_pct REAL,
                max_adverse_pct REAL,
                max_favorable_pct REAL,
                directional_hit INTEGER,
                evaluation_note TEXT
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agg_outcomes_pending "
            "ON aggregated_outcomes(evaluated_at, signal_ts)"
        )
        
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

    async def insert_aggregated_outcome(
        self,
        *,
        signal_ts: int,
        symbol: str,
        action: str,
        baseline_action: str,
        confidence: float,
        risk: str,
        price_at_signal: Optional[float],
        reasons: Optional[List[str]],
        horizon_sec: int,
        council_enabled: bool,
        council_changed: bool,
        sent_telegram: bool,
    ) -> Optional[int]:
        """Record one aggregated decision for later horizon evaluation."""
        sym = (symbol or "").strip()
        if not sym:
            return None
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO aggregated_outcomes (
                        signal_ts, symbol, action, baseline_action,
                        council_enabled, council_changed,
                        confidence, risk, price_at_signal, reasons_json,
                        horizon_sec, sent_telegram
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_ts,
                        sym,
                        action.upper(),
                        baseline_action.upper(),
                        1 if council_enabled else 0,
                        1 if council_changed else 0,
                        confidence,
                        risk,
                        price_at_signal,
                        json.dumps(reasons or []),
                        int(horizon_sec),
                        1 if sent_telegram else 0,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                self.logger.error("insert_aggregated_outcome failed: %s", e, exc_info=True)
                return None
            finally:
                conn.close()

    async def evaluate_pending_aggregated_outcomes(
        self,
        now_ts: int,
        *,
        direction_threshold_pct: float = 0.05,
        candle_timeframe: str = "1m",
    ) -> int:
        """
        Fill metrics for rows whose horizon has passed. Rows without candles
        are left pending (evaluated_at stays NULL).

        Returns number of rows updated.
        """
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            updated = 0
            try:
                cursor.execute(
                    """
                    SELECT id, symbol, signal_ts, horizon_sec, price_at_signal, action
                    FROM aggregated_outcomes
                    WHERE evaluated_at IS NULL
                      AND (? >= signal_ts + horizon_sec)
                    """,
                    (now_ts,),
                )
                rows = cursor.fetchall()
                for row in rows:
                    oid = row["id"]
                    symbol = row["symbol"]
                    signal_ts = int(row["signal_ts"])
                    horizon_sec = int(row["horizon_sec"])
                    price_at_signal = row["price_at_signal"]
                    action = row["action"]
                    end_ts = signal_ts + horizon_sec

                    candles: List[Dict[str, Any]] = []
                    for sym_variant in (
                        symbol,
                        symbol.upper(),
                        symbol.lower(),
                    ):
                        cursor.execute(
                            """
                            SELECT timestamp, open, high, low, close, volume
                            FROM candles
                            WHERE symbol = ? AND timeframe = ?
                              AND timestamp >= ? AND timestamp <= ?
                            ORDER BY timestamp ASC
                            """,
                            (sym_variant, candle_timeframe, signal_ts, end_ts),
                        )
                        raw = cursor.fetchall()
                        if raw:
                            candles = [dict(r) for r in raw]
                            break
                    if not candles:
                        continue

                    entry = float(price_at_signal) if price_at_signal else 0.0
                    if entry <= 0:
                        entry = float(candles[0]["close"])

                    metrics = compute_path_metrics(
                        entry,
                        action,
                        candles,
                        direction_threshold_pct=direction_threshold_pct,
                    )
                    if not metrics:
                        cursor.execute(
                            """
                            UPDATE aggregated_outcomes
                            SET evaluated_at = ?, evaluation_note = ?
                            WHERE id = ?
                            """,
                            (now_ts, "no_metrics", oid),
                        )
                        updated += 1
                        continue

                    dh = metrics["directional_hit"]
                    dh_sql = dh if dh is not None else None

                    cursor.execute(
                        """
                        UPDATE aggregated_outcomes
                        SET evaluated_at = ?,
                            close_at_horizon = ?,
                            return_pct = ?,
                            max_adverse_pct = ?,
                            max_favorable_pct = ?,
                            directional_hit = ?,
                            evaluation_note = NULL
                        WHERE id = ?
                        """,
                        (
                            now_ts,
                            metrics["close_at_horizon"],
                            metrics["return_pct"],
                            metrics["max_adverse_pct"],
                            metrics["max_favorable_pct"],
                            dh_sql,
                            oid,
                        ),
                    )
                    updated += 1

                conn.commit()
                return updated
            except Exception as e:
                self.logger.error("evaluate_pending_aggregated_outcomes failed: %s", e, exc_info=True)
                return updated
            finally:
                conn.close()

    async def get_aggregated_outcomes_summary(
        self, since_ts: int
    ) -> Dict[str, Any]:
        """Aggregate stats for evaluated rows since since_ts (unix)."""
        async with self.lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT action,
                           COUNT(*) AS n,
                           AVG(return_pct) AS avg_ret,
                           AVG(CASE WHEN directional_hit = 1 THEN 1.0 ELSE 0.0 END) AS hit_rate
                    FROM aggregated_outcomes
                    WHERE evaluated_at IS NOT NULL
                      AND evaluated_at >= ?
                      AND directional_hit IS NOT NULL
                    GROUP BY action
                    """,
                    (since_ts,),
                )
                by_action = [dict(r) for r in cursor.fetchall()]

                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total_evaluated,
                        SUM(CASE WHEN council_changed = 1 THEN 1 ELSE 0 END) AS council_changes,
                        AVG(
                            CASE directional_hit
                                WHEN 1 THEN 1.0
                                WHEN 0 THEN 0.0
                                ELSE NULL
                            END
                        ) AS overall_hit_rate
                    FROM aggregated_outcomes
                    WHERE evaluated_at IS NOT NULL
                      AND evaluated_at >= ?
                    """,
                    (since_ts,),
                )
                overall = dict(cursor.fetchone() or {})

                cursor.execute(
                    """
                    SELECT COUNT(*) FROM aggregated_outcomes
                    WHERE signal_ts >= ? AND evaluated_at IS NULL
                    """,
                    (since_ts,),
                )
                pending = cursor.fetchone()[0]

                return {
                    "since_ts": since_ts,
                    "by_action": by_action,
                    "overall": overall,
                    "pending_horizon": pending,
                }
            except Exception as e:
                self.logger.error("get_aggregated_outcomes_summary failed: %s", e, exc_info=True)
                return {"since_ts": since_ts, "error": str(e)}
            finally:
                conn.close()

