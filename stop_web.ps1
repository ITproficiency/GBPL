# ==============================================================================
# PostureCare AI System - Stop Web Server & AI Tracking Script (Windows)
# ==============================================================================

Write-Host "======================================================================" -ForegroundColor Red
Write-Host "🛑 PostureCare AI - Stopping Web Server & AI Engine..." -ForegroundColor Red
Write-Host "======================================================================" -ForegroundColor Red

# Free port 8080
try {
    $conns = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
    if ($conns) {
        Write-Host "🔹 Terminating process on port 8080..." -ForegroundColor Yellow
        foreach ($conn in $conns) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
} catch {}

# Stop Python AI Tracking Engine process
Write-Host "🔹 Stopping background AI tracking processes..." -ForegroundColor Yellow
try {
    Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%blink_counter_and_EAR_plot.py%'" -ErrorAction SilentlyContinue | Remove-CimInstance -ErrorAction SilentlyContinue
} catch {}

# Stop uvicorn web server process
Write-Host "🔹 Stopping Uvicorn Web Server..." -ForegroundColor Yellow
try {
    Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%uvicorn%'" -ErrorAction SilentlyContinue | Remove-CimInstance -ErrorAction SilentlyContinue
} catch {}

Write-Host "----------------------------------------------------------------------" -ForegroundColor Gray
Write-Host "✅ All PostureCare Web & Tracking processes have been STOPPED successfully!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Red
