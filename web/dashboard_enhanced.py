"""
dashboard_enhanced.py - Расширенный веб-дашборд с фильтрами и графиками
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import sqlite3
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from core.health_check import HealthCheck
from core.metrics import Metrics
from core.analytics import Analytics

app = FastAPI(title="Crypto Analytics Dashboard Enhanced")

# Глобальные переменные
db: Database = None
health_check: HealthCheck = None
metrics: Metrics = None
analytics: Analytics = None
active_connections: List[WebSocket] = []


@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    global db, health_check, metrics, analytics
    db = Database("crypto_analytics.db")
    health_check = HealthCheck()
    metrics = Metrics(db)
    analytics = Analytics(db)


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Главная страница расширенного дашборда"""
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e27;
            color: #e0e0e0;
            padding: 20px;
        }
        .container { max-width: 1600px; margin: 0 auto; }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 30px;
            border-radius: 15px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
        }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; }
        .filters {
            background: #1a1f3a;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            border: 1px solid #2a2f4a;
        }
        .filters input, .filters select {
            padding: 10px;
            border-radius: 8px;
            border: 1px solid #2a2f4a;
            background: #252a4a;
            color: #e0e0e0;
            font-size: 14px;
        }
        .filters input { flex: 1; min-width: 200px; }
        .filters select { min-width: 150px; }
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
        .btn {
            padding: 10px 20px;
            border-radius: 8px;
            border: none;
            background: #667eea;
            color: white;
            cursor: pointer;
            font-weight: 600;
        }
        .btn:hover { background: #5568d3; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Crypto Analytics Dashboard</h1>
            <p>Мониторинг в реальном времени с расширенными возможностями</p>
        </div>
        
        <div class="filters">
            <input type="text" id="search-input" placeholder="🔍 Поиск по символу или сообщению...">
            <select id="agent-filter">
                <option value="">Все агенты</option>
                <option value="market">Market</option>
                <option value="onchain">OnChain</option>
                <option value="liquidity">Liquidity</option>
                <option value="shitcoin">Shitcoin</option>
                <option value="emergency">Emergency</option>
                <option value="aggregator">Aggregator</option>
            </select>
            <select id="type-filter">
                <option value="">Все типы</option>
                <option value="BUY">BUY</option>
                <option value="SELL">SELL</option>
                <option value="EXIT">EXIT</option>
                <option value="WAIT">WAIT</option>
            </select>
            <select id="hours-filter">
                <option value="1">Последний час</option>
                <option value="6">Последние 6 часов</option>
                <option value="24" selected>Последние 24 часа</option>
                <option value="168">Последняя неделя</option>
            </select>
            <button class="btn" onclick="applyFilters()">Применить</button>
            <button class="btn" onclick="exportData()">📥 Экспорт</button>
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
        
        <div class="chart-container">
            <h2 style="margin-bottom: 20px;">📊 Сигналы по агентам</h2>
            <canvas id="agentsChart"></canvas>
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
        let signalsChart, agentsChart;
        let currentFilters = { hours: 24 };
        
        ws.onopen = () => {
            console.log('WebSocket подключен');
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            updateDashboard(data);
        };
        
        function applyFilters() {
            currentFilters = {
                search: document.getElementById('search-input').value,
                agent: document.getElementById('agent-filter').value,
                type: document.getElementById('type-filter').value,
                hours: parseInt(document.getElementById('hours-filter').value)
            };
            loadData();
        }
        
        function loadData() {
            let url = `/api/dashboard?hours=${currentFilters.hours}`;
            if (currentFilters.agent) url += `&agent_type=${currentFilters.agent}`;
            if (currentFilters.type) url += `&signal_type=${currentFilters.type}`;
            
            fetch(url)
                .then(r => r.json())
                .then(updateDashboard);
        }
        
        function exportData() {
            window.open(`/api/export/json?hours=${currentFilters.hours}`, '_blank');
        }
        
        function updateDashboard(data) {
            if (data.stats) {
                document.getElementById('total-signals').textContent = data.stats.total_signals || 0;
                document.getElementById('signals-24h').textContent = data.stats.signals_24h || 0;
                document.getElementById('active-agents').textContent = data.stats.active_agents || 0;
                document.getElementById('avg-confidence').textContent = 
                    (data.stats.avg_confidence || 0).toFixed(1) + '%';
            }
            
            if (data.signals) {
                let filtered = data.signals;
                if (currentFilters.search) {
                    const search = currentFilters.search.toLowerCase();
                    filtered = filtered.filter(s => 
                        (s.symbol && s.symbol.toLowerCase().includes(search)) ||
                        (s.message && s.message.toLowerCase().includes(search))
                    );
                }
                updateSignalsTable(filtered);
            }
            
            if (data.chart_data) {
                updateChart(data.chart_data);
            }
            
            if (data.agents_data) {
                updateAgentsChart(data.agents_data);
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
                const actionClass = `signal-${(signal.action || signal.signal_type || '').toLowerCase()}`;
                return `
                    <tr>
                        <td>${time}</td>
                        <td><strong>${signal.symbol || 'N/A'}</strong></td>
                        <td><span class="${actionClass}">${signal.action || signal.signal_type || 'N/A'}</span></td>
                        <td>${signal.price ? signal.price.toFixed(4) : 'N/A'}</td>
                        <td>${signal.confidence ? (signal.confidence * 100).toFixed(1) + '%' : 'N/A'}</td>
                        <td>${signal.risk || 'N/A'}</td>
                        <td>${signal.agent_type || 'N/A'}</td>
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
        
        function updateAgentsChart(agentsData) {
            if (!agentsChart) {
                const ctx = document.getElementById('agentsChart').getContext('2d');
                agentsChart = new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: agentsData.labels || [],
                        datasets: [{
                            data: agentsData.data || [],
                            backgroundColor: [
                                '#667eea', '#764ba2', '#f093fb', '#4facfe', '#00f2fe', '#43e97b'
                            ]
                        }]
                    },
                    options: {
                        responsive: true,
                        plugins: {
                            legend: { position: 'right', labels: { color: '#888' } }
                        }
                    }
                });
            } else {
                agentsChart.data.labels = agentsData.labels || [];
                agentsChart.data.datasets[0].data = agentsData.data || [];
                agentsChart.update();
            }
        }
        
        // Запрос начальных данных
        loadData();
        
        // Автообновление каждые 30 секунд
        setInterval(loadData, 30000);
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


@app.get("/api/dashboard")
async def get_dashboard_data(
    hours: int = Query(24, ge=1, le=720),
    agent_type: Optional[str] = None,
    signal_type: Optional[str] = None
):
    """Получение данных для расширенного дашборда"""
    try:
        # Статистика
        stats = await metrics.get_statistics(hours)
        
        # Последние сигналы
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        since = int((datetime.utcnow() - timedelta(hours=hours)).timestamp())
        query = "SELECT * FROM signals WHERE timestamp > ?"
        params = [since]
        
        if agent_type:
            query += " AND agent_type = ?"
            params.append(agent_type)
        
        if signal_type:
            query += " AND signal_type = ?"
            params.append(signal_type)
        
        query += " ORDER BY timestamp DESC LIMIT 100"
        cursor.execute(query, params)
        
        signals = []
        for row in cursor.fetchall():
            data = json.loads(row['data']) if row['data'] else {}
            signals.append({
                'symbol': row['symbol'],
                'action': row['signal_type'],
                'price': data.get('price') or row.get('price'),
                'confidence': data.get('confidence'),
                'risk': data.get('risk') or row.get('risk'),
                'agent_type': row['agent_type'],
                'timestamp': row['timestamp'],
                'message': row['message']
            })
        
        # Данные для графика сигналов
        cursor.execute("""
            SELECT strftime('%H', datetime(timestamp, 'unixepoch')) as hour, COUNT(*) as count
            FROM signals
            WHERE timestamp > ?
            GROUP BY hour
            ORDER BY hour
        """, (since,))
        
        hour_data = {row['hour']: row['count'] for row in cursor.fetchall()}
        chart_labels = [f"{i:02d}:00" for i in range(24)]
        chart_data = [hour_data.get(f"{i:02d}", 0) for i in range(24)]
        
        # Данные по агентам
        cursor.execute("""
            SELECT agent_type, COUNT(*) as count
            FROM signals
            WHERE timestamp > ?
            GROUP BY agent_type
        """, (since,))
        
        agents_data = cursor.fetchall()
        agents_labels = [row['agent_type'] for row in agents_data]
        agents_counts = [row['count'] for row in agents_data]
        
        conn.close()
        
        return {
            'stats': {
                'total_signals': stats.get('total_signals', 0),
                'signals_24h': stats.get('total_signals', 0),
                'active_agents': len([s for s in stats.get('by_agent', {}).values() if s > 0]),
                'avg_confidence': 75.0
            },
            'signals': signals,
            'chart_data': {
                'labels': chart_labels,
                'data': chart_data
            },
            'agents_data': {
                'labels': agents_labels,
                'data': agents_counts
            }
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
            data = await get_dashboard_data(24)
            await websocket.send_json(data)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        active_connections.remove(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

