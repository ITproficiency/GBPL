#!/bin/bash
# ==============================================================================
# PostureCare AI System - 1-Click Launch Script (macOS / Linux)
# ==============================================================================

echo "======================================================================"
echo "🚀 PostureCare AI System - Starting Web Dashboard & AI Tracking"
echo "======================================================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find Python executable
PYTHON_EXEC="python3"
for candidate in "$SCRIPT_DIR/myenv/bin/python" "$SCRIPT_DIR/tracking_AI/.venv/bin/python" "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/venv/bin/python" "/Users/yangtao/.platformio/penv/bin/python"; do
    if [ -f "$candidate" ]; then
        PYTHON_EXEC="$candidate"
        break
    fi
done

echo "🔹 Using Python: $PYTHON_EXEC"

echo "🔹 Freeing port 8080 & clearing old processes..."
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
pkill -9 -f blink_counter_and_EAR_plot.py 2>/dev/null || true

echo "🚀 Launching FastAPI Web Dashboard Server on http://localhost:8080/static/index.html..."
cd "$SCRIPT_DIR/dashboard/gPBL/backend" || exit
$PYTHON_EXEC -m uvicorn main:app --host 0.0.0.0 --port 8080 > /dev/null 2>&1 &
SERVER_PID=$!
sleep 2

cleanup() {
    echo ""
    echo "🛑 Stopping Web Dashboard Server (PID: $SERVER_PID)..."
    kill -9 $SERVER_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$SCRIPT_DIR"
CAM_ARG="$1"
if [ -z "$CAM_ARG" ]; then
    # Dynamically fetch active ESP32 IP from Firebase RTDB if available
    DISCOVERED_IP=$(curl -s "https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app/sensor_data/esp32_ip.json" 2>/dev/null | tr -d '"')
    if [ -n "$DISCOVERED_IP" ] && [ "$DISCOVERED_IP" != "null" ]; then
        CAM_ARG="http://${DISCOVERED_IP}:80/stream"
    else
        CAM_ARG="http://192.168.1.39:80/stream"
    fi
fi

if [ "$CAM_ARG" = "--dashboard-only" ] || [ "$CAM_ARG" = "--web-only" ]; then
    echo "ℹ️  Running in Dashboard-Only mode. Press Ctrl+C to stop."
    echo "✅ PostureCare AI Dashboard is LIVE at: http://localhost:8080/static/index.html"
    wait $SERVER_PID
else
    echo "📷 Initializing AI Tracking Engine (Source: $CAM_ARG)..."
    echo "----------------------------------------------------------------------"
    echo "✅ PostureCare AI Dashboard is LIVE at: http://localhost:8080/static/index.html"
    $PYTHON_EXEC tracking_AI/blink_counter_and_EAR_plot.py "$CAM_ARG"
fi
