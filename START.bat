@echo off
chcp 65001 >nul
title Crypto Analytics System
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
echo   Main app runs in this window. API and dashboard start separately.
echo ============================================================
echo.

timeout /t 2 >nul

echo Starting REST API on port 8001...
start "Crypto Analytics - REST API" cmd /k "cd /d %~dp0 && python web/api.py"

timeout /t 1 >nul

echo Starting dashboard on port 8000...
start "Crypto Analytics - Dashboard" cmd /k "cd /d %~dp0 && python web/dashboard_enhanced.py"

timeout /t 1 >nul

echo.
echo   Dashboard: http://localhost:8000
echo   API:       http://localhost:8001
echo   Docs:      http://localhost:8001/docs
echo.
echo   Press Ctrl+C in this window to stop the main process.
echo ============================================================
echo.

python main.py

pause
