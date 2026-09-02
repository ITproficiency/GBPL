# One-command local demo (Windows)
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
Set-Location $Backend

function Write-Step($msg) { Write-Host ">> $msg" -ForegroundColor Cyan }

Write-Host ""
Write-Host "PostureCare gPBL - local demo" -ForegroundColor Green
Write-Host ""

$script:PyExe = $null
$script:PyArgs = @()

function Get-PythonVersion([string]$exe, [string[]]$exeArgs) {
    try {
        if ($exeArgs -and $exeArgs.Count -gt 0) {
            $v = & $exe @exeArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } else {
            $v = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        }
        if ($LASTEXITCODE -ne 0) { return $null }
        return ("$v").Trim()
    } catch {
        return $null
    }
}

function Resolve-PythonSoft {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($minor in @(12, 11, 10)) {
            $ver = Get-PythonVersion "py" @("-3.$minor")
            if ($ver -in @("3.10", "3.11", "3.12")) {
                $script:PyExe = "py"
                $script:PyArgs = @("-3.$minor")
                return
            }
        }
        $ver13 = Get-PythonVersion "py" @("-3.13")
        if ($ver13) {
            $majorMinor = $ver13.Split(".")
            if ([int]$majorMinor[0] -eq 3 -and [int]$majorMinor[1] -ge 10) {
                $script:PyExe = "py"
                $script:PyArgs = @("-3.13")
                return
            }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $ver = Get-PythonVersion "python" @()
        if ($ver) {
            $parts = $ver.Split(".")
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10) {
                $script:PyExe = "python"
                $script:PyArgs = @()
                return
            }
        }
        Write-Error "Python $ver is too old. Dashboard needs Python 3.10+ (3.13 allowed here). Install Python 3.11 from python.org and add it to PATH."
    }
    Write-Error "Python 3.10+ not found. Install Python 3.11 from python.org (recommended) and add it to PATH."
}

function Invoke-ResolvedPython([string[]]$extraArgs) {
    $all = @()
    if ($script:PyArgs -and $script:PyArgs.Count -gt 0) { $all += $script:PyArgs }
    if ($extraArgs -and $extraArgs.Count -gt 0) { $all += $extraArgs }
    & $script:PyExe @all
}

Resolve-PythonSoft

Write-Step "Creating virtualenv (backend/.venv) if needed..."
$venvDir = Join-Path $Backend ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvCfg = Join-Path $venvDir "pyvenv.cfg"

if ((Test-Path $venvDir) -and -not (Test-Path $venvCfg)) {
    Write-Host "   Broken .venv detected - recreating..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
}

if (-not (Test-Path $venvPython)) {
    Invoke-ResolvedPython @("-m", "venv", $venvDir)
    if (-not (Test-Path $venvPython)) {
        Write-Error "Failed to create virtualenv at backend/.venv"
    }
} else {
    Write-Host "   Reusing backend/.venv" -ForegroundColor DarkGray
}

$reqHash = (Get-FileHash "requirements.txt" -Algorithm SHA256).Hash
$hashFile = Join-Path $venvDir ".requirements.hash"
$prevHash = ""
if (Test-Path $hashFile) {
    $prevHash = (Get-Content $hashFile -Raw).Trim()
}
if ($prevHash -ne $reqHash) {
    Write-Step "Installing dependencies..."
    & $venvPython -m pip install -q --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed. Try: Remove-Item -Recurse backend\.venv then run again."
    }
    & $venvPython -m pip install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install requirements.txt"
    }
    Set-Content -Path $hashFile -Value $reqHash -NoNewline
}

Write-Step "Checking config files..."
if (-not (Test-Path ".env.example")) {
    Write-Error "MISSING: $Backend\.env.example"
}
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "   Created .env from .env.example" -ForegroundColor Yellow
}

if (-not (Test-Path "data\rules.json.example")) {
    Write-Error "MISSING: $Backend\data\rules.json.example"
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
