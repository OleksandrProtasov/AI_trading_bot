"""
api.py - Расширенный REST API
"""
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from typing import Optional, List
from datetime import datetime, timedelta
import sqlite3
import json
import csv
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from core.metrics import Metrics
from core.health_check import HealthCheck

app = FastAPI(title="Crypto Analytics API", version="1.0.0")

# Глобальные переменные
db: Database = None
metrics: Metrics = None
health_check: HealthCheck = None


@app.on_event("startup")
async def startup_event():
    global db, metrics, health_check
    db = Database("crypto_analytics.db")
    metrics = Metrics(db)
    health_check = HealthCheck()


# ========== СИГНАЛЫ ==========

@app.get("/api/signals")
async def get_signals(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = None,
    agent_type: Optional[str] = None,
    signal_type: Optional[str] = None,
    hours: int = Query(24, ge=1, le=720)
):
    """Получение списка сигналов с фильтрацией"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        
        query = "SELECT * FROM signals WHERE timestamp > ?"
        params = [since]
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        
        if agent_type:
            query += " AND agent_type = ?"
            params.append(agent_type.lower())
        
        if signal_type:
            query += " AND signal_type = ?"
            params.append(signal_type.lower())
        
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        signals = []
        for row in rows:
            data = json.loads(row['data']) if row['data'] else {}
            signals.append({
                'id': row['id'],
                'timestamp': row['timestamp'],
                'agent_type': row['agent_type'],
                'symbol': row['symbol'],
                'signal_type': row['signal_type'],
                'priority': row['priority'],
                'message': row['message'],
                'data': data,
                'sent_to_telegram': bool(row['sent_to_telegram'])
            })
        
        # Общее количество
        count_query = query.replace("ORDER BY timestamp DESC LIMIT ? OFFSET ?", "")
        cursor.execute(count_query, params[:-2])
        total = len(cursor.fetchall())
        
        conn.close()
        
        return {
            'signals': signals,
            'total': total,
            'limit': limit,
            'offset': offset
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/{signal_id}")
async def get_signal(signal_id: int):
    """Получение конкретного сигнала"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Signal not found")
        
        data = json.loads(row['data']) if row['data'] else {}
        signal = {
            'id': row['id'],
            'timestamp': row['timestamp'],
            'agent_type': row['agent_type'],
            'symbol': row['symbol'],
            'signal_type': row['signal_type'],
            'priority': row['priority'],
            'message': row['message'],
            'data': data,
            'sent_to_telegram': bool(row['sent_to_telegram'])
        }
        
        conn.close()
        return signal
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== МЕТРИКИ ==========

@app.get("/api/metrics")
async def get_metrics(hours: int = Query(24, ge=1, le=720)):
    """Получение метрик системы"""
    try:
        stats = await metrics.get_statistics(hours)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics/summary")
async def get_metrics_summary():
    """Краткая сводка метрик"""
    try:
        summary = metrics.get_summary()
        stats = await metrics.get_statistics(24)
        return {
            'summary': summary,
            'stats_24h': {
                'total_signals': stats.get('total_signals', 0),
                'by_agent': stats.get('by_agent', {}),
                'by_type': stats.get('by_type', {}),
                'errors': stats.get('errors', 0)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== АГЕНТЫ ==========

@app.get("/api/agents")
async def get_agents():
    """Список всех агентов"""
    return {
        'agents': [
            {'name': 'market', 'description': 'Market Agent - анализ рыночных данных'},
            {'name': 'onchain', 'description': 'OnChain Agent - отслеживание whale транзакций'},
            {'name': 'liquidity', 'description': 'Liquidity Agent - анализ ликвидности'},
            {'name': 'shitcoin', 'description': 'Shitcoin Agent - поиск пампов/дампов'},
            {'name': 'emergency', 'description': 'Emergency Agent - срочные сигналы'},
            {'name': 'aggregator', 'description': 'Aggregator Agent - агрегация сигналов'}
        ]
    }


@app.get("/api/agents/status")
async def get_agents_status():
    """Статус всех агентов"""
    try:
        if health_check:
            status = await health_check.check_health()
            return {
                'agents': {name: status.value for name, status in status.items()},
                'summary': health_check.get_status_summary(),
                'timestamp': int(datetime.utcnow().timestamp())
            }
        return {'agents': {}, 'summary': 'Health check не инициализирован'}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== СВЕЧИ ==========

@app.get("/api/candles")
async def get_candles(
    symbol: str = Query(..., description="Символ (например, BTCUSDT)"),
    timeframe: str = Query("1m", description="Таймфрейм"),
    limit: int = Query(100, ge=1, le=1000),
    hours: int = Query(24, ge=1, le=720)
):
    """Получение свечей"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        
        cursor.execute("""
            SELECT * FROM candles 
            WHERE symbol = ? AND timeframe = ? AND timestamp > ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (symbol.upper(), timeframe, since, limit))
        
        rows = cursor.fetchall()
        candles = []
        for row in rows:
            candles.append({
                'timestamp': row['timestamp'],
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume']
            })
        
        conn.close()
        return {'symbol': symbol, 'timeframe': timeframe, 'candles': candles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== ЭКСПОРТ ==========

@app.get("/api/export/csv")
async def export_csv(
    hours: int = Query(24, ge=1, le=720),
    symbol: Optional[str] = None
):
    """Экспорт сигналов в CSV"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        
        query = "SELECT * FROM signals WHERE timestamp > ?"
        params = [since]
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        
        query += " ORDER BY timestamp DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Заголовки
        writer.writerow(['ID', 'Timestamp', 'Agent', 'Symbol', 'Type', 'Priority', 'Message'])
        
        # Данные
        for row in rows:
            writer.writerow([
                row['id'],
                datetime.fromtimestamp(row['timestamp']).isoformat(),
                row['agent_type'],
                row['symbol'],
                row['signal_type'],
                row['priority'],
                row['message']
            ])
        
        conn.close()
        
        output.seek(0)
        return JSONResponse(
            content={'csv': output.getvalue()},
            media_type="application/json"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export/json")
async def export_json(
    hours: int = Query(24, ge=1, le=720),
    symbol: Optional[str] = None
):
    """Экспорт сигналов в JSON"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        
        query = "SELECT * FROM signals WHERE timestamp > ?"
        params = [since]
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        
        query += " ORDER BY timestamp DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        signals = []
        for row in rows:
            data = json.loads(row['data']) if row['data'] else {}
            signals.append({
                'id': row['id'],
                'timestamp': row['timestamp'],
                'agent_type': row['agent_type'],
                'symbol': row['symbol'],
                'signal_type': row['signal_type'],
                'priority': row['priority'],
                'message': row['message'],
                'data': data
            })
        
        conn.close()
        return {'signals': signals, 'count': len(signals)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== ПОИСК ==========

@app.get("/api/search")
async def search(
    q: str = Query(..., description="Поисковый запрос"),
    limit: int = Query(50, ge=1, le=100)
):
    """Поиск по сигналам"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = """
            SELECT * FROM signals 
            WHERE message LIKE ? OR symbol LIKE ? OR signal_type LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        search_term = f"%{q}%"
        cursor.execute(query, (search_term, search_term, search_term, limit))
        
        rows = cursor.fetchall()
        signals = []
        for row in rows:
            data = json.loads(row['data']) if row['data'] else {}
            signals.append({
                'id': row['id'],
                'timestamp': row['timestamp'],
                'agent_type': row['agent_type'],
                'symbol': row['symbol'],
                'signal_type': row['signal_type'],
                'priority': row['priority'],
                'message': row['message'],
                'data': data
            })
        
        conn.close()
        return {'query': q, 'results': signals, 'count': len(signals)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== СТАТИСТИКА ==========

@app.get("/api/stats/symbols")
async def get_symbols_stats(hours: int = Query(24, ge=1, le=720)):
    """Статистика по символам"""
    try:
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        
        cursor.execute("""
            SELECT symbol, COUNT(*) as count, 
                   COUNT(DISTINCT signal_type) as types,
                   MAX(timestamp) as last_signal
            FROM signals
            WHERE timestamp > ? AND symbol IS NOT NULL
            GROUP BY symbol
            ORDER BY count DESC
            LIMIT 20
        """, (since,))
        
        stats = []
        for row in cursor.fetchall():
            stats.append({
                'symbol': row['symbol'],
                'signals_count': row['count'],
                'signal_types': row['types'],
                'last_signal': row['last_signal']
            })
        
        conn.close()
        return {'symbols': stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/performance")
async def get_performance_stats():
    """Статистика производительности"""
    try:
        stats = await metrics.get_statistics(24)
        return {
            'uptime_hours': stats.get('uptime_hours', 0),
            'total_signals': stats.get('total_signals', 0),
            'sent_to_telegram': stats.get('sent_to_telegram', 0),
            'errors': stats.get('errors', 0),
            'by_agent': stats.get('by_agent', {}),
            'by_type': stats.get('by_type', {})
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

