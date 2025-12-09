"""
dashboard.py - FastAPI веб-сервер для дашборда
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import sqlite3
from typing import List, Dict
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from core.health_check import HealthCheck
from core.metrics import Metrics

app = FastAPI(title="Crypto Analytics Dashboard")

# Глобальные переменные
db: Database = None
health_check: HealthCheck = None
metrics: Metrics = None
active_connections: List[WebSocket] = []


@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    global db, health_check, metrics
    db = Database("crypto_analytics.db")
    health_check = HealthCheck()
    metrics = Metrics(db)


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Главная страница дашборда"""
    html_content = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Analytics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #0a0e27;
            color: #e0e0e0;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 30px;
            border-radius: 15px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .header p { opacity: 0.9; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: #1a1f3a;
            padding: 25px;
            border-radius: 12px;
            border: 1px solid #2a2f4a;
            transition: transform 0.2s;
        }
        .stat-card:hover { transform: translateY(-5px); }
        .stat-card h3 {
            font-size: 0.9em;
            color: #888;
            margin-bottom: 10px;
            text-transform: uppercase;
        }
        .stat-card .value {
            font-size: 2.5em;
            font-weight: bold;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .chart-container {
            background: #1a1f3a;
            padding: 25px;
            border-radius: 12px;
            margin-bottom: 30px;
            border: 1px solid #2a2f4a;
        }
        .signals-table {
            background: #1a1f3a;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #2a2f4a;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th {
            background: #2a2f4a;
            padding: 15px;
            text-align: left;
            font-weight: 600;
            color: #667eea;
        }
        td {
            padding: 15px;
            border-top: 1px solid #2a2f4a;
        }
        tr:hover { background: #252a4a; }
        .status-badge {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }
        .status-healthy { background: #10b981; color: white; }
        .status-degraded { background: #f59e0b; color: white; }
        .status-unhealthy { background: #ef4444; color: white; }
        .signal-buy { color: #10b981; }
        .signal-sell { color: #ef4444; }
        .signal-exit { color: #f59e0b; }
        .signal-wait { color: #6b7280; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Crypto Analytics Dashboard</h1>
            <p>Мониторинг в реальном времени</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <h3>Всего сигналов</h3>
                <div class="value" id="total-signals">0</div>
            </div>
            <div class="stat-card">
                <h3>Сигналов за 24ч</h3>
                <div class="value" id="signals-24h">0</div>
            </div>
            <div class="stat-card">
                <h3>Активных агентов</h3>
                <div class="value" id="active-agents">0</div>
            </div>
            <div class="stat-card">
                <h3>Уверенность</h3>
                <div class="value" id="avg-confidence">0%</div>
            </div>
        </div>
        
        <div class="chart-container">
            <h2 style="margin-bottom: 20px;">📈 Сигналы по времени</h2>
            <canvas id="signalsChart"></canvas>
        </div>
        
        <div class="signals-table">
            <h2 style="padding: 20px; margin: 0;">📋 Последние сигналы</h2>
            <table>
                <thead>
                    <tr>
                        <th>Время</th>
                        <th>Символ</th>
                        <th>Действие</th>
                        <th>Цена</th>
                        <th>Уверенность</th>
                        <th>Риск</th>
                        <th>Агент</th>
                    </tr>
                </thead>
                <tbody id="signals-tbody">
                    <tr><td colspan="7" style="text-align: center; padding: 40px;">Загрузка...</td></tr>
                </tbody>
            </table>
        </div>
    </div>
    
    <script>
        const ws = new WebSocket(`ws://${window.location.host}/ws`);
        let signalsChart;
        
        ws.onopen = () => {
            console.log('WebSocket подключен');
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            updateDashboard(data);
        };
        
        function updateDashboard(data) {
            if (data.stats) {
                document.getElementById('total-signals').textContent = data.stats.total_signals || 0;
                document.getElementById('signals-24h').textContent = data.stats.signals_24h || 0;
                document.getElementById('active-agents').textContent = data.stats.active_agents || 0;
                document.getElementById('avg-confidence').textContent = 
                    (data.stats.avg_confidence || 0).toFixed(1) + '%';
            }
            
            if (data.signals) {
                updateSignalsTable(data.signals);
            }
            
            if (data.chart_data) {
                updateChart(data.chart_data);
            }
        }
        
        function updateSignalsTable(signals) {
            const tbody = document.getElementById('signals-tbody');
            if (signals.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 40px;">Нет сигналов</td></tr>';
                return;
            }
            
            tbody.innerHTML = signals.map(signal => {
                const time = new Date(signal.timestamp * 1000).toLocaleString('ru-RU');
                const actionClass = `signal-${signal.action.toLowerCase()}`;
                return `
                    <tr>
                        <td>${time}</td>
                        <td><strong>${signal.symbol}</strong></td>
                        <td><span class="${actionClass}">${signal.action}</span></td>
                        <td>${signal.price ? signal.price.toFixed(4) : 'N/A'}</td>
                        <td>${(signal.confidence * 100).toFixed(1)}%</td>
                        <td>${signal.risk || 'N/A'}</td>
                        <td>${signal.agent_type}</td>
                    </tr>
                `;
            }).join('');
        }
        
        function updateChart(chartData) {
            if (!signalsChart) {
                const ctx = document.getElementById('signalsChart').getContext('2d');
                signalsChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: chartData.labels || [],
                        datasets: [{
                            label: 'Сигналы',
                            data: chartData.data || [],
                            borderColor: '#667eea',
                            backgroundColor: 'rgba(102, 126, 234, 0.1)',
                            tension: 0.4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            y: { beginAtZero: true, ticks: { color: '#888' }, grid: { color: '#2a2f4a' } },
                            x: { ticks: { color: '#888' }, grid: { color: '#2a2f4a' } }
                        }
                    }
                });
            } else {
                signalsChart.data.labels = chartData.labels || [];
                signalsChart.data.datasets[0].data = chartData.data || [];
                signalsChart.update();
            }
        }
        
        // Запрос начальных данных
        fetch('/api/dashboard')
            .then(r => r.json())
            .then(updateDashboard);
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


@app.get("/api/dashboard")
async def get_dashboard_data():
    """Получение данных для дашборда"""
    try:
        # Статистика
        stats = await metrics.get_statistics(24)
        
        # Последние сигналы
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT symbol, signal_type, price, confidence, risk, agent_type, timestamp
            FROM signals
            WHERE timestamp > ?
            ORDER BY timestamp DESC
            LIMIT 50
        """, (int((datetime.utcnow() - timedelta(hours=24)).timestamp()),))
        
        signals = []
        for row in cursor.fetchall():
            signals.append({
                'symbol': row['symbol'],
                'action': row['signal_type'],
                'price': row['price'],
                'confidence': row['confidence'] or 0,
                'risk': row['risk'],
                'agent_type': row['agent_type'],
                'timestamp': row['timestamp']
            })
        
        conn.close()
        
        # Данные для графика
        chart_data = {
            'labels': [f"{i}:00" for i in range(24)],
            'data': [0] * 24  # Упрощенная версия
        }
        
        return {
            'stats': {
                'total_signals': stats.get('total_signals', 0),
                'signals_24h': stats.get('total_signals', 0),
                'active_agents': len([s for s in stats.get('by_agent', {}).values() if s > 0]),
                'avg_confidence': 75.0  # Упрощенная версия
            },
            'signals': signals,
            'chart_data': chart_data
        }
    except Exception as e:
        return {'error': str(e)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket для real-time обновлений"""
    await websocket.accept()
    active_connections.append(websocket)
    
    try:
        while True:
            # Отправляем обновления каждые 5 секунд
            data = await get_dashboard_data()
            await websocket.send_json(data)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        active_connections.remove(websocket)


@app.get("/api/agents/status")
async def get_agents_status():
    """Статус агентов"""
    if health_check:
        status = await health_check.check_health()
        return {
            'agents': {name: status.value for name, status in status.items()},
            'summary': health_check.get_status_summary()
        }
    return {'agents': {}, 'summary': 'Health check не инициализирован'}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

