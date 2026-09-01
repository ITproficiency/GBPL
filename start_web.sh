#!/bin/bash
# ==============================================================================
# PostureCare AI System - 1-Click Start Script
# ==============================================================================

echo "======================================================================"
echo "🚀 PostureCare AI System - Starting Web Dashboard & AI Tracking"
echo "======================================================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXEC="/Users/yangtao/.platformio/penv/bin/python"

if [ ! -f "$PYTHON_EXEC" ]; then
    PYTHON_EXEC="python3"
fi

echo "🔹 Freeing port 8080 & clearing old processes..."
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
pkill -9 -f blink_counter_and_EAR_plot.py 2>/dev/null || true

echo "🚀 Launching FastAPI Web Dashboard Server on http://localhost:8080/static/index.html..."
cd "$SCRIPT_DIR/dashboard/gPBL/backend" || exit
$PYTHON_EXEC -m uvicorn main:app --host 0.0.0.0 --port 8080 > /dev/null 2>&1 &
sleep 3

cd "$SCRIPT_DIR"
CAM_URL="$1"
if [ -z "$CAM_URL" ]; then
    # Dynamically fetch active ESP32 IP from Firebase RTDB if available
    DISCOVERED_IP=$(curl -s "https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app/sensor_data/esp32_ip.json" 2>/dev/null | tr -d '"')
    if [ -n "$DISCOVERED_IP" ] && [ "$DISCOVERED_IP" != "null" ]; then
        CAM_URL="http://${DISCOVERED_IP}:80/stream"
    else
        CAM_URL="http://192.168.1.39:80/stream"
    fi
fi

echo "📷 Initializing AI Tracking Engine for dynamic source: $CAM_URL..."
curl -s -X POST http://localhost:8080/api/tracking/start \
     -H "Content-Type: application/json" \
     -d "{\"source\":\"$CAM_URL\"}"

echo ""
echo "----------------------------------------------------------------------"
echo "✅ PostureCare AI Dashboard is LIVE at: http://localhost:8080/static/index.html"
echo "======================================================================"
