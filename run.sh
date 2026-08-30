#!/bin/bash
# ==============================================================================
# PostureCare AI System - 1-Click Launch Script (macOS / Linux)
# ==============================================================================

echo "======================================================================"
echo "🛡️  PostureCare AI System - Launching Web Dashboard & AI Tracking"
echo "======================================================================"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXEC="$SCRIPT_DIR/myenv/bin/python"

if [ ! -f "$PYTHON_EXEC" ]; then
    PYTHON_EXEC="python3"
fi

echo "🔹 Using Python: $PYTHON_EXEC"

echo "🔹 Freeing port 8080..."
lsof -ti:8080 | xargs kill -9 2>/dev/null || true

echo "🚀 Starting FastAPI Web Dashboard Server on http://localhost:8080/dashboard..."
cd "$SCRIPT_DIR/dashboard/gPBL/backend"
$PYTHON_EXEC -m uvicorn main:app --host 0.0.0.0 --port 8080 > /dev/null 2>&1 &
SERVER_PID=$!
sleep 2

echo "🌐 Web Dashboard is running live at: http://localhost:8080/dashboard"

cd "$SCRIPT_DIR"
CAM_ARG="${1:-0}"
echo "📷 Launching Python AI Tracking (Source: $CAM_ARG)..."
echo "----------------------------------------------------------------------"
$PYTHON_EXEC tracking_AI/blink_counter_and_EAR_plot.py "$CAM_ARG"

kill -9 $SERVER_PID 2>/dev/null || true
