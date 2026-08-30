# ==============================================================================
# PostureCare AI System - 1-Click Launch Script (Windows PowerShell)
# ==============================================================================

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "🛡️  PostureCare AI System - Launching Web Dashboard & AI Tracking" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExec = Join-Path $ScriptDir "myenv\Scripts\python.exe"

if (-not (Test-Path $PythonExec)) {
    $PythonExec = "python"
}

Write-Host "🔹 Using Python: $PythonExec" -ForegroundColor Yellow

Write-Host "🚀 Starting FastAPI Web Dashboard Server on http://localhost:8080/dashboard..." -ForegroundColor Green
$BackendDir = Join-Path $ScriptDir "dashboard\gPBL\backend"
$ServerJob = Start-Job -ScriptBlock {
    param($py, $dir)
    Set-Location $dir
    & $py -m uvicorn main:app --host 0.0.0.0 --port 8080
} -ArgumentList $PythonExec, $BackendDir

Start-Sleep -Seconds 3

Write-Host "🌐 Web Dashboard is running live at: http://localhost:8080/dashboard" -ForegroundColor Green

$CamArg = "0"
if ($args.Count -gt 0) {
    $CamArg = $args[0]
}

Write-Host "📷 Launching Python AI Tracking (Source: $CamArg)..." -ForegroundColor Yellow
Write-Host "----------------------------------------------------------------------"
Set-Location $ScriptDir
& $PythonExec tracking_AI/blink_counter_and_EAR_plot.py $CamArg

Stop-Job $ServerJob
Remove-Job $ServerJob
