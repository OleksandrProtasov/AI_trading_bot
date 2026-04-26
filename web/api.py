"""FastAPI REST surface for signals, metrics, export, and search."""
from contextlib import asynccontextmanager

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
from core.runtime_paths import resolved_database_path
from core.backtest_portfolio import (
    BacktestConfig,
    DEFAULT_RAW_AGENT_TYPES,
    run_aggregator_backtest,
    run_backtest_compare,
    run_raw_signals_backtest,
)

# module singletons
db: Database = None
metrics: Metrics = None
health_check: HealthCheck = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, metrics, health_check
    db = Database(resolved_database_path())
    metrics = Metrics(db)
    health_check = HealthCheck()
    yield


app = FastAPI(title="Crypto Analytics API", version="1.0.0", lifespan=lifespan)


# --- Signals ---

@app.get("/api/signals")
async def get_signals(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = None,
    agent_type: Optional[str] = None,
    signal_type: Optional[str] = None,
    hours: int = Query(24, ge=1, le=720)
):
    """List signals with optional filters."""
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
        
        # total count
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
    """Fetch one signal by id."""
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


@app.get("/api/outcomes/summary")
async def outcomes_summary(hours: int = Query(168, ge=1, le=8760)):
    """Hit rate and avg return by action for evaluated aggregated signals."""
    try:
        since_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        return await db.get_aggregated_outcomes_summary(since_ts)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Metrics ---

@app.get("/api/metrics")
async def get_metrics(hours: int = Query(24, ge=1, le=720)):
    """Aggregate counters from SQLite."""
    try:
        stats = await metrics.get_statistics(hours)
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/backtest/aggregator")
async def backtest_aggregator(
    hours: int = Query(24 * 30, ge=1, le=24 * 365 * 5),
    min_confidence: float = Query(0.55, ge=0.0, le=1.0),
    horizon_minutes: int = Query(240, ge=5, le=24 * 60),
    fee_bps: float = Query(5.0, ge=0.0, le=100.0),
    max_open: int = Query(1, ge=1, le=20),
):
    """Historical backtest over saved aggregator signals."""
    try:
        since_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        cfg = BacktestConfig(
            min_confidence=min_confidence,
            horizon_minutes=horizon_minutes,
            fee_bps_per_side=fee_bps,
            max_open_positions=max_open,
        )
        return run_aggregator_backtest(db.db_path, start_ts=since_ts, cfg=cfg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/backtest/raw")
async def backtest_raw(
    hours: int = Query(24 * 30, ge=1, le=24 * 365 * 5),
    min_confidence: float = Query(0.55, ge=0.0, le=1.0),
    horizon_minutes: int = Query(240, ge=5, le=24 * 60),
    fee_bps: float = Query(5.0, ge=0.0, le=100.0),
    max_open: int = Query(1, ge=1, le=20),
    raw_include_stables: bool = Query(
        True,
        description="If false, skip signals on stablecoin symbols",
    ),
    raw_agents: Optional[str] = Query(
        None,
        description="Comma-separated agent_type list (default: market,liquidity,onchain,emergency)",
    ),
):
    """Historical backtest over raw agent signals (heuristic side from signal_type)."""
    try:
        since_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        cfg = BacktestConfig(
            min_confidence=min_confidence,
            horizon_minutes=horizon_minutes,
            fee_bps_per_side=fee_bps,
            max_open_positions=max_open,
            raw_include_stables=raw_include_stables,
        )
        agents = (
            tuple(a.strip() for a in raw_agents.split(",") if a.strip())
            if raw_agents
            else DEFAULT_RAW_AGENT_TYPES
        )
        return run_raw_signals_backtest(
            db.db_path, start_ts=since_ts, cfg=cfg, agent_types=agents
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/backtest/compare")
async def backtest_compare(
    hours: int = Query(24 * 30, ge=1, le=24 * 365 * 5),
    min_confidence: float = Query(0.55, ge=0.0, le=1.0),
    horizon_minutes: int = Query(240, ge=5, le=24 * 60),
    fee_bps: float = Query(5.0, ge=0.0, le=100.0),
    max_open: int = Query(1, ge=1, le=20),
    raw_include_stables: bool = Query(True),
    raw_agents: Optional[str] = Query(None),
):
    """Compare aggregator vs raw-agent heuristic backtest on the same window."""
    try:
        since_ts = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        cfg = BacktestConfig(
            min_confidence=min_confidence,
            horizon_minutes=horizon_minutes,
            fee_bps_per_side=fee_bps,
            max_open_positions=max_open,
            raw_include_stables=raw_include_stables,
        )
        agents = (
            tuple(a.strip() for a in raw_agents.split(",") if a.strip())
            if raw_agents
            else DEFAULT_RAW_AGENT_TYPES
        )
        return run_backtest_compare(
            db.db_path, start_ts=since_ts, cfg=cfg, raw_agent_types=agents
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics/summary")
async def get_metrics_summary():
    """Compact metrics snapshot."""
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


# --- Agents ---

@app.get("/api/agents")
async def get_agents():
    """Static catalog of agent roles."""
    return {
        'agents': [
            {"name": "market", "description": "Market data + TA-style heuristics"},
            {"name": "onchain", "description": "DEX flow / whale-style volume heuristics"},
            {"name": "liquidity", "description": "Order book imbalance and liquidity pockets"},
            {"name": "shitcoin", "description": "High-volatility DEX meme scanner"},
            {"name": "emergency", "description": "Fast volume/price/liquidity alerts"},
            {"name": "aggregator", "description": "Weighted BUY/SELL/EXIT/WAIT synthesis"},
        ]
    }


@app.get("/api/agents/status")
async def get_agents_status():
    """Optional hook for live agent heartbeats (placeholder)."""
    try:
        if health_check:
            status = await health_check.check_health()
            return {
                'agents': {name: status.value for name, status in status.items()},
                'summary': health_check.get_status_summary(),
                'timestamp': int(datetime.utcnow().timestamp())
            }
        return {"agents": {}, "summary": "Health check not wired in API process"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Candles ---

@app.get("/api/candles")
async def get_candles(
    symbol: str = Query(..., description="Symbol, e.g. BTCUSDT"),
    timeframe: str = Query("1m", description="Candle interval"),
    limit: int = Query(100, ge=1, le=1000),
    hours: int = Query(24, ge=1, le=720)
):
    """Return recent OHLCV rows."""
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


# --- Export ---

@app.get("/api/export/csv")
async def export_csv(
    hours: int = Query(24, ge=1, le=720),
    symbol: Optional[str] = None
):
    """Download signals as CSV."""
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
        
        # header row
        writer.writerow(['ID', 'Timestamp', 'Agent', 'Symbol', 'Type', 'Priority', 'Message'])
        
        # body rows
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
    """Download signals as JSON."""
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


# --- Search ---

@app.get("/api/search")
async def search(
    q: str = Query(..., description="Free-text query"),
    limit: int = Query(50, ge=1, le=100)
):
    """Simple LIKE search across stored signals."""
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


# --- Statistics ---

@app.get("/api/stats/symbols")
async def get_symbols_stats(hours: int = Query(24, ge=1, le=720)):
    """Per-symbol counts for a time window."""
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
    """Placeholder performance stats."""
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

    _port = 8001
    print(
        f"\n  REST API: http://127.0.0.1:{_port}\n"
        f"  OpenAPI:  http://127.0.0.1:{_port}/docs\n",
        flush=True,
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=_port,
        log_level="info",
    )




