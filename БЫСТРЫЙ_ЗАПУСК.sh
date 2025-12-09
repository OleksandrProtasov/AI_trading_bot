#!/bin/bash

echo "========================================"
echo "Запуск Crypto Analytics System"
echo "========================================"
echo ""

echo "[1/3] Проверка зависимостей..."
python3 -c "import fastapi, uvicorn, telegram" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Установка зависимостей..."
    pip3 install -r requirements.txt
fi

echo ""
echo "[2/3] Запуск основной системы..."
gnome-terminal -- bash -c "python3 main.py; exec bash" 2>/dev/null || \
xterm -e "python3 main.py" 2>/dev/null || \
echo "Запустите вручную: python3 main.py"

echo ""
echo "[3/3] Запуск REST API (порт 8001)..."
gnome-terminal -- bash -c "python3 web/api.py; exec bash" 2>/dev/null || \
xterm -e "python3 web/api.py" 2>/dev/null || \
echo "Запустите вручную: python3 web/api.py"

echo ""
echo "[4/4] Запуск веб-дашборда (порт 8000)..."
gnome-terminal -- bash -c "python3 web/dashboard_enhanced.py; exec bash" 2>/dev/null || \
xterm -e "python3 web/dashboard_enhanced.py" 2>/dev/null || \
echo "Запустите вручную: python3 web/dashboard_enhanced.py"

echo ""
echo "========================================"
echo "Все компоненты запущены!"
echo "========================================"
echo ""
echo "Веб-дашборд: http://localhost:8000"
echo "REST API: http://localhost:8001"
echo "API Docs: http://localhost:8001/docs"
echo ""

