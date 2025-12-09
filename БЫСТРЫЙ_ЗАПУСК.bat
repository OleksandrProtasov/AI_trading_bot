@echo off
echo ========================================
echo Запуск Crypto Analytics System
echo ========================================
echo.

echo [1/3] Проверка зависимостей...
python -c "import fastapi, uvicorn, telegram" 2>nul
if errorlevel 1 (
    echo Установка зависимостей...
    pip install -r requirements.txt
)

echo.
echo [2/3] Запуск основной системы...
echo Откройте новый терминал для веб-интерфейсов
echo.
start cmd /k "python main.py"

echo.
echo [3/3] Запуск REST API (порт 8001)...
start cmd /k "python web/api.py"

echo.
echo [4/4] Запуск веб-дашборда (порт 8000)...
start cmd /k "python web/dashboard_enhanced.py"

echo.
echo ========================================
echo Все компоненты запущены!
echo ========================================
echo.
echo Веб-дашборд: http://localhost:8000
echo REST API: http://localhost:8001
echo API Docs: http://localhost:8001/docs
echo.
pause

