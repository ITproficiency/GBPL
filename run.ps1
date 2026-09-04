# ==============================================================================
# PostureCare AI System - 1-Click Launch Script (Windows PowerShell)
# ==============================================================================

# Ensure UTF-8 output encoding for console text & emojis
try {
    $OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "🚀 PostureCare AI System - Starting Web Dashboard & AI Tracking" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find Python executable from common venv paths or system python
$PythonCandidatePaths = @(
    (Join-Path $ScriptDir "myenv\Scripts\python.exe"),
    (Join-Path $ScriptDir "tracking_AI\.venv\Scripts\python.exe"),
    (Join-Path $ScriptDir ".venv\Scripts\python.exe"),
    (Join-Path $ScriptDir "venv\Scripts\python.exe")
)

$PythonExec = "python"
foreach ($path in $PythonCandidatePaths) {
    if (Test-Path $path) {
        $PythonExec = $path
        break
    }
}

Write-Host "🔹 Using Python: $PythonExec" -ForegroundColor Yellow

# Free port 8080 & kill old AI tracking processes
try {
    Write-Host "🔹 Freeing port 8080 & clearing old processes..." -ForegroundColor Yellow
    $conns = Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%blink_counter_and_EAR_plot.py%'" -ErrorAction SilentlyContinue | Remove-CimInstance -ErrorAction SilentlyContinue
} catch {}

Write-Host "🚀 Launching FastAPI Web Dashboard Server on http://localhost:8080/static/index.html..." -ForegroundColor Green
$BackendDir = Join-Path $ScriptDir "dashboard\gPBL\backend"
$ServerJob = Start-Job -ScriptBlock {
    param($py, $dir)
    Set-Location $dir
    & $py -m uvicorn main:app --host 0.0.0.0 --port 8080
} -ArgumentList $PythonExec, $BackendDir

Start-Sleep -Seconds 3

$CamArg = ""
if ($args.Count -gt 0) {
    $CamArg = $args[0]
}

if (-not $CamArg) {
    # Dynamically fetch active ESP32 IP from Firebase RTDB if available
    try {
        $resp = Invoke-RestMethod -Uri "https://gpbl-iot-llms-default-rtdb.asia-southeast1.firebasedatabase.app/sensor_data/esp32_ip.json" -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp -and $resp -ne "null") {
            $CamArg = "http://${resp}:80/stream"
        } else {
            $CamArg = "http://192.168.1.39:80/stream"
        }
    } catch {
        $CamArg = "http://192.168.1.39:80/stream"
    }
}

Write-Host "----------------------------------------------------------------------" -ForegroundColor Gray
Write-Host "✅ PostureCare AI Dashboard is LIVE at: http://localhost:8080/static/index.html" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan

if ($CamArg -eq "--dashboard-only" -or $CamArg -eq "--web-only") {
    Write-Host "ℹ️  Running in Dashboard-Only mode. Press Ctrl+C to stop." -ForegroundColor Cyan
    try {
        while ($true) { Start-Sleep -Seconds 1 }
    } finally {
        Write-Host "🛑 Stopping Web Dashboard Server..." -ForegroundColor Yellow
        Stop-Job $ServerJob -ErrorAction SilentlyContinue
        Remove-Job $ServerJob -ErrorAction SilentlyContinue
    }
} else {
    try {
        Write-Host "📷 Initializing AI Tracking Engine (Source: $CamArg)..." -ForegroundColor Yellow
        Write-Host "----------------------------------------------------------------------"
        Set-Location $ScriptDir
        & $PythonExec tracking_AI/blink_counter_and_EAR_plot.py $CamArg
    } finally {
        Write-Host "🛑 Stopping Web Dashboard Server..." -ForegroundColor Yellow
        Stop-Job $ServerJob -ErrorAction SilentlyContinue
        Remove-Job $ServerJob -ErrorAction SilentlyContinue
    }
}
