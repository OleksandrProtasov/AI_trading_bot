@echo off
chcp 65001 >nul
set PYTHONUNBUFFERED=1
title MAIN BOT - logs in this window
color 0A

echo.
echo ============================================================
echo   Crypto Analytics System - quick start
echo ============================================================
echo.

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+
    pause
    exit /b 1
)
echo OK: Python found

echo.
echo [2/4] Checking dependencies...
python -c "import fastapi, uvicorn, telegram, websockets, aiohttp" 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: pip install failed
        pause
        exit /b 1
    )
    echo OK: Dependencies installed
) else (
    echo OK: Core packages present
)

echo.
echo [3/4] Checking config...
if exist "config.py" (
    echo OK: config.py found
) else (
    echo WARNING: config.py missing — copy config.py.example to config.py
)

echo.
echo [4/4] Starting services...
echo.
echo   Main bot logs INFO to this window (see also logs\ folder).
echo   API + Dashboard open in separate windows — keep them open.
echo ============================================================
echo.

timeout /t 2 >nul

echo Starting REST API on port 8001 ^(new window^)...
start "REST API :8001" cmd /k "cd /d %~dp0 && set PYTHONUNBUFFERED=1 && title REST API :8001 && color 0B && python -u web/api.py"

timeout /t 1 >nul

echo Starting dashboard on port 8000 ^(new window^)...
start "Dashboard :8000" cmd /k "cd /d %~dp0 && set PYTHONUNBUFFERED=1 && title Dashboard :8000 && color 0E && python -u web/dashboard_enhanced.py"

timeout /t 1 >nul

echo.
echo   Dashboard: http://127.0.0.1:8000
echo   API:       http://127.0.0.1:8001
echo   Docs:      http://127.0.0.1:8001/docs
echo.
echo   If the site does not open: wait 2-3 sec, check firewall, try 127.0.0.1
echo   THIS WINDOW = main bot ^(agents, Telegram, aggregation^).
echo   Every ~60s: one INFO line with signal/candle counts ^(see config activity_log_interval_sec^).
echo   Anytime snapshot:  python bot_activity.py
echo   Press Ctrl+C here to stop the bot. Close other windows to stop web.
echo ============================================================
echo.

python -u main.py

pause
