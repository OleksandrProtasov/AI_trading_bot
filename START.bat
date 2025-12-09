@echo off
chcp 65001 >nul
title Crypto Analytics System
color 0A

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🚀 Crypto Analytics System - Быстрый запуск 🚀      ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

echo [1/4] Проверка Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python не найден! Установите Python 3.8+
    pause
    exit /b 1
)
echo ✅ Python найден

echo.
echo [2/4] Проверка зависимостей...
python -c "import fastapi, uvicorn, telegram, websockets, aiohttp" 2>nul
if errorlevel 1 (
    echo ⚠️  Некоторые зависимости отсутствуют
    echo 📦 Установка зависимостей...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ❌ Ошибка установки зависимостей
        pause
        exit /b 1
    )
    echo ✅ Зависимости установлены
) else (
    echo ✅ Все зависимости установлены
)

echo.
echo [3/4] Проверка конфигурации...
if exist "config.py" (
    echo ✅ config.py найден
) else (
    echo ⚠️  config.py не найден, используется config.py.example
)

echo.
echo [4/4] Запуск системы...
echo.
echo ═══════════════════════════════════════════════════════════
echo   📊 Основная система будет запущена в этом окне
echo   🌐 Веб-интерфейсы будут запущены в отдельных окнах
echo ═══════════════════════════════════════════════════════════
echo.

timeout /t 2 >nul

echo 🚀 Запуск REST API (порт 8001)...
start "Crypto Analytics - REST API" cmd /k "cd /d %~dp0 && python web/api.py"

timeout /t 1 >nul

echo 🌐 Запуск веб-дашборда (порт 8000)...
start "Crypto Analytics - Dashboard" cmd /k "cd /d %~dp0 && python web/dashboard_enhanced.py"

timeout /t 1 >nul

echo.
echo ═══════════════════════════════════════════════════════════
echo   ✅ Все компоненты запущены!
echo ═══════════════════════════════════════════════════════════
echo.
echo   📊 Веб-дашборд: http://localhost:8000
echo   🔌 REST API:    http://localhost:8001
echo   📚 API Docs:    http://localhost:8001/docs
echo.
echo   Нажмите Ctrl+C для остановки основной системы
echo ═══════════════════════════════════════════════════════════
echo.

python main.py

pause

