# ==============================================================================
# PostureCare - one-run (Windows): dashboard + tracking AI
# Two venvs, created once, reused after that:
#   dashboard/gPBL/backend/.venv  - FastAPI
#   tracking_AI/.venv             - MediaPipe / OpenCV
# ==============================================================================

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Write-Step($msg) { Write-Host ">> $msg" -ForegroundColor Cyan }

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

function Resolve-PythonHard {
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
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $ver = Get-PythonVersion "python" @()
        if ($ver -in @("3.10", "3.11", "3.12")) {
            $script:PyExe = "python"
            $script:PyArgs = @()
            return
        }
        Write-Error "Python $ver found, but tracking_AI needs Python 3.10-3.12 (MediaPipe). Install Python 3.11 from python.org and add it to PATH."
    }
    Write-Error "Python 3.10-3.12 not found. Install Python 3.11 from python.org (recommended) and add it to PATH."
}

function Invoke-ResolvedPython([string[]]$extraArgs) {
    $all = @()
    if ($script:PyArgs -and $script:PyArgs.Count -gt 0) { $all += $script:PyArgs }
    if ($extraArgs -and $extraArgs.Count -gt 0) { $all += $extraArgs }
    & $script:PyExe @all
}

function Test-VenvPythonOk([string]$pyPath) {
    if (-not (Test-Path $pyPath)) { return $false }
    try {
        $ok = & $pyPath -c "import sys; print('ok' if (3,10) <= sys.version_info[:2] <= (3,12) else 'no')"
        return (("$ok").Trim() -eq "ok")
    } catch {
        return $false
    }
}

function Ensure-Venv([string]$venvDir, [string]$requirements) {
    $py = Join-Path $venvDir "Scripts\python.exe"
    $cfg = Join-Path $venvDir "pyvenv.cfg"
    $hashFile = Join-Path $venvDir ".requirements.hash"

    if ((Test-Path $venvDir) -and -not (Test-Path $cfg)) {
        Write-Host "   Broken venv at $venvDir - recreating once" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $venvDir
    } elseif ((Test-Path $py) -and -not (Test-VenvPythonOk $py)) {
        Write-Host "   Venv Python outside 3.10-3.12 at $venvDir - recreating once" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $venvDir
    }

    if (-not (Test-Path $py)) {
        Write-Step "Creating $venvDir (first run only)"
        Invoke-ResolvedPython @("-m", "venv", $venvDir)
        if (-not (Test-Path $py)) {
            Write-Error "Failed to create venv at $venvDir"
        }
    } else {
        Write-Host "   Reusing $venvDir" -ForegroundColor DarkGray
    }

    $reqHash = (Get-FileHash $requirements -Algorithm SHA256).Hash
    $prev = ""
    if (Test-Path $hashFile) {
        $prev = (Get-Content $hashFile -Raw).Trim()
    }
    if ($prev -ne $reqHash) {
        Write-Step "Installing deps for $venvDir"
        & $py -m pip install -q --upgrade pip
        if ($LASTEXITCODE -ne 0) { Write-Error "pip upgrade failed in $venvDir" }
        & $py -m pip install -q -r $requirements
        if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed for $requirements" }
        Set-Content -Path $hashFile -Value $reqHash -NoNewline
    }

    return $py
}

function Clear-Port8080([string]$backendDir) {
    $ownerPids = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort 8080 -ErrorAction Stop
        $ownerPids = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        return
    }
    if ($ownerPids.Count -eq 0) { return }

    $ours = @()
    $foreign = @()
    $backendMarker = "\dashboard\gPBL\backend\.venv"
    foreach ($procId in $ownerPids) {
        if ($procId -in @(0, 4, $PID)) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $cl = [string]$proc.CommandLine
        $exe = [string]$proc.ExecutablePath
        $isOurs = $false
        if ($cl -and ($cl -match "uvicorn") -and ($cl -match "main:app")) { $isOurs = $true }
        if ($exe -and ($exe -like "*$backendMarker*")) { $isOurs = $true }
        if ($cl -and ($cl -like "*$backendMarker*")) { $isOurs = $true }
        if ($isOurs) {
            $ours += $procId
        } else {
            $foreign += $procId
        }
    }

    if ($foreign.Count -gt 0) {
        $first = $foreign[0]
        $info = Get-CimInstance Win32_Process -Filter "ProcessId=$first" -ErrorAction SilentlyContinue
        Write-Host "Port 8080 is in use by another process (PID: $($foreign -join ', '))." -ForegroundColor Red
        if ($info) {
            Write-Host "Command: $($info.CommandLine)" -ForegroundColor Red
        }
        Write-Host "Stop that process yourself. Refusing to steal 8080." -ForegroundColor Red
        exit 1
    }

    foreach ($procId in $ours) {
        Write-Host "   Stopping leftover uvicorn on 8080 (PID $procId)" -ForegroundColor DarkGray
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}

function Wait-Port8080([int]$timeoutSec = 10) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $iar = $tcp.BeginConnect("127.0.0.1", 8080, $null, $null)
            $ok = $iar.AsyncWaitHandle.WaitOne(400, $false)
            if ($ok -and $tcp.Connected) {
                $tcp.Close()
                return $true
            }
            $tcp.Close()
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Show-UvicornLog([string]$backendDir) {
    Write-Host "FastAPI did not bind to :8080 in time. Last log lines:" -ForegroundColor Red
    foreach ($name in @("uvicorn.log", "uvicorn.err.log")) {
        $f = Join-Path $backendDir $name
        if (Test-Path $f) {
            Write-Host "--- $name ---" -ForegroundColor Yellow
            Get-Content $f -Tail 40
        }
    }
}

Resolve-PythonHard

$BackendDir = Join-Path $Root "dashboard\gPBL\backend"
$BackendPy = Ensure-Venv (Join-Path $BackendDir ".venv") (Join-Path $BackendDir "requirements.txt")
$TrackPy = Ensure-Venv (Join-Path $Root "tracking_AI\.venv") (Join-Path $Root "tracking_AI\requirements.txt")

Write-Step "Checking config files..."
$envExample = Join-Path $BackendDir ".env.example"
$envFile = Join-Path $BackendDir ".env"
if (-not (Test-Path $envExample)) {
    Write-Error "MISSING: $envExample"
}
if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "   Created .env from .env.example" -ForegroundColor Yellow
}

$dataDir = Join-Path $BackendDir "data"
$rulesExample = Join-Path $dataDir "rules.json.example"
$rulesFile = Join-Path $dataDir "rules.json"
if (-not (Test-Path $rulesExample)) {
    Write-Error "MISSING: $rulesExample"
}
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}
if (-not (Test-Path $rulesFile)) {
    Copy-Item $rulesExample $rulesFile
    Write-Host "   Created data/rules.json from example" -ForegroundColor Yellow
}

$keyFile = Join-Path $BackendDir "serviceAccountKey.json"
if (-not (Test-Path $keyFile)) {
    Write-Host ""
    Write-Host "MISSING: backend/serviceAccountKey.json" -ForegroundColor Red
    Write-Host "  Firebase Console -> Service accounts -> Generate new private key"
    Write-Host "  Save as: backend/serviceAccountKey.json"
    Write-Host "  Or: copy serviceAccountKey.json.example serviceAccountKey.json"
    exit 1
}

$envRaw = Get-Content $envFile -Raw
if ($envRaw -match "OPENROUTER_API_KEY=sk-or-v1-your-key") {
    Write-Host "   Note: OPENROUTER_API_KEY still default - LLM uses mock advice until you edit .env" -ForegroundColor Yellow
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "PostureCare - dashboard + AI tracking" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Web Python: $BackendPy" -ForegroundColor Yellow
Write-Host "CV  Python: $TrackPy" -ForegroundColor Yellow

Write-Host "Freeing port 8080 (our uvicorn only)..." -ForegroundColor DarkGray
Clear-Port8080 $BackendDir

$outLog = Join-Path $BackendDir "uvicorn.log"
$errLog = Join-Path $BackendDir "uvicorn.err.log"
foreach ($f in @($outLog, $errLog)) {
    if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
}

Write-Host "Starting FastAPI on http://localhost:8080/dashboard ..." -ForegroundColor Green
# Pass each token separately: a single string with spaces is quoted by
# Start-Process on Windows PowerShell 5.1, so python would see one argv.
$uvArgs = @("-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080")
$ServerProc = Start-Process -FilePath $BackendPy `
    -ArgumentList $uvArgs `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -PassThru `
    -WindowStyle Hidden

try {
    if (-not (Wait-Port8080 10)) {
        Show-UvicornLog $BackendDir
        exit 1
    }
    Write-Host "Dashboard: http://localhost:8080/dashboard" -ForegroundColor Green

    $CamArg = "0"
    if ($args.Count -gt 0) {
        $CamArg = $args[0]
    }

    Write-Host "Launching AI tracking (source: $CamArg)..." -ForegroundColor Yellow
    Write-Host "----------------------------------------------------------------------"
    Set-Location $Root
    $trackScript = Join-Path $Root 'tracking_AI\blink_counter_and_EAR_plot.py'
    & $TrackPy $trackScript $CamArg
} finally {
    if ($ServerProc -and -not $ServerProc.HasExited) {
        Stop-Process -Id $ServerProc.Id -Force -ErrorAction SilentlyContinue
    }
}
