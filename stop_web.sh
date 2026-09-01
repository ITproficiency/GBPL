#!/bin/bash
# ==============================================================================
# PostureCare AI System - Stop Web Server & AI Tracking Script
# ==============================================================================

echo "======================================================================"
echo "🛑 PostureCare AI - Stopping Web Server & AI Engine..."
echo "======================================================================"

# Free port 8080
if lsof -ti:8080 >/dev/null 2>&1; then
    echo "🔹 Terminating process on port 8080..."
    lsof -ti:8080 | xargs kill -9 2>/dev/null || true
fi

# Stop Python AI Tracking Engine process
echo "🔹 Stopping background AI tracking processes..."
pkill -9 -f blink_counter_and_EAR_plot.py 2>/dev/null || true

# Stop uvicorn web server process
echo "🔹 Stopping Uvicorn Web Server..."
pkill -9 -f uvicorn 2>/dev/null || true

echo "----------------------------------------------------------------------"
echo "✅ All PostureCare Web & Tracking processes have been STOPPED successfully!"
echo "======================================================================"
