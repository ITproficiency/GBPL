# One-command local demo (Windows)
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
Set-Location $Backend

function Write-Step($msg) { Write-Host ">> $msg" -ForegroundColor Cyan }

Write-Host ""
Write-Host "PostureCare gPBL - local demo" -ForegroundColor Green
Write-Host ""

# Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python not found. Install Python 3.11+ and add to PATH."
}

Write-Step "Creating virtualenv (backend/.venv) if needed..."
$venvDir = Join-Path $Backend ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvCfg = Join-Path $venvDir "pyvenv.cfg"

if ((Test-Path $venvDir) -and -not (Test-Path $venvCfg)) {
    Write-Host "   Broken .venv detected - recreating..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
}

if (-not (Test-Path $venvPython)) {
    python -m venv $venvDir
    if (-not (Test-Path $venvPython)) {
        Write-Error "Failed to create virtualenv at backend/.venv"
    }
}

& $venvPython -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Error "ensurepip failed. Remove backend\.venv and run again."
}

Write-Step "Installing dependencies..."
& $venvPython -m pip install -q --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed. Try: Remove-Item -Recurse backend\.venv then run again."
}
& $venvPython -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install requirements.txt"
}

Write-Step "Checking config files..."
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "   Created .env from .env.example" -ForegroundColor Yellow
}

if (-not (Test-Path "data")) {
    New-Item -ItemType Directory -Path "data" | Out-Null
}
if (-not (Test-Path "data\rules.json")) {
    Copy-Item "data\rules.json.example" "data\rules.json"
    Write-Host "   Created data/rules.json from example" -ForegroundColor Yellow
}

if (-not (Test-Path "serviceAccountKey.json")) {
    Write-Host ""
    Write-Host "MISSING: backend/serviceAccountKey.json" -ForegroundColor Red
    Write-Host "  Firebase Console -> Service accounts -> Generate new private key"
    Write-Host "  Save as: backend/serviceAccountKey.json"
    Write-Host "  Or: copy serviceAccountKey.json.example serviceAccountKey.json"
    exit 1
}

$envRaw = Get-Content ".env" -Raw
if ($envRaw -match "OPENROUTER_API_KEY=sk-or-v1-your-key") {
    Write-Host "   Note: OPENROUTER_API_KEY still default - LLM uses mock advice until you edit .env" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Dashboard: http://localhost:8080/dashboard" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

& $venvPython -m uvicorn main:app --host 0.0.0.0 --port 8080
