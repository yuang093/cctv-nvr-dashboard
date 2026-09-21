# run_web.ps1
# ===========
# Windows PowerShell entry script for v2 Web UI (Flask).
#
# Usage (foreground, development / smoke test):
#   .\run_web.ps1
#
# Usage (Task Scheduler "Action" tab):
#   Program: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
#   Args:    -ExecutionPolicy Bypass -File "C:\nvr\run_web.ps1"
#
# Environment variables (optional, override defaults below):
#   NVR_WEB_HOST  default 0.0.0.0     (LAN-friendly; 127.0.0.1 = 本機 only)
#   NVR_WEB_PORT  default 8444        (avoids clash with NVR's 8443)
#   NVR_DB_PATH   default <project_dir>\nvr_scan.db
#
#   ⚠️ 對外暴露時務必加 reverse proxy (nginx / caddy) + HTTPS

$ErrorActionPreference = "Continue"

# === Customizable section (edit for your deployment) ===
$PROJECT_DIR = (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$VENV_PY     = Join-Path $PROJECT_DIR "venv\Scripts\python.exe"

$WebHost = if ($env:NVR_WEB_HOST) { $env:NVR_WEB_HOST } else { "127.0.0.1" }   # Day-0: 預設只綁本機,避免公網意外暴露
$WebPort = if ($env:NVR_WEB_PORT) { $env:NVR_WEB_PORT } else { "8444" }
$DbPath  = if ($env:NVR_DB_PATH)  { $env:NVR_DB_PATH  } else { Join-Path $PROJECT_DIR "nvr_scan.db" }

# === Main flow ===
if (-not (Test-Path $PROJECT_DIR)) {
    Write-Error "[FATAL] Project directory not found: $PROJECT_DIR"
    exit 2
}
Set-Location $PROJECT_DIR

if (-not (Test-Path $VENV_PY)) {
    Write-Error "[FATAL] venv python not found: $VENV_PY"
    Write-Error "Please run:  python -m venv venv  ;  venv\Scripts\activate  ;  pip install -r requirements-web.txt"
    exit 3
}

# === Pre-flight: kill leftover Flask on the target port ===
# Avoids the previous-session silent-bind-failure trap (a stale Python on
# the same port silently holds it; new Flask looks like it started but is
# actually unreachable and the old process still serves stale code).
$existing = Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    foreach ($conn in $existing) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq "python") {
            Write-Host "[INFO] Killing leftover Flask on port $WebPort (PID $($proc.Id), started $($proc.StartTime))"
            Stop-Process -Id $proc.Id -Force
        }
    }
    Start-Sleep -Milliseconds 500
}

# Export for web.app to consume
$env:NVR_WEB_HOST = $WebHost
$env:NVR_WEB_PORT = $WebPort
$env:NVR_DB_PATH  = $DbPath

# Week 5 #013：HTTPS 模式（NVR_HTTPS_ENABLED=1 → Werkzeug adhoc SSL）
$HttpsFlag = ""
if ($env:NVR_HTTPS_ENABLED -eq "1") {
    $HttpsFlag = "--https=adhoc"
    Write-Host "[INFO] HTTPS mode: Werkzeug adhoc SSL"
    Write-Host "[INFO] Starting NVR Web UI at https://${WebHost}:${WebPort}"
} else {
    Write-Host "[INFO] Starting NVR Web UI at http://${WebHost}:${WebPort}"
}
Write-Host "[INFO] DB: $DbPath"
Write-Host "[INFO] Press Ctrl+C to stop"

# Foreground run; ctrl+c kills the Flask process
if ($HttpsFlag -ne "") {
    & $VENV_PY -m web.app $HttpsFlag
} else {
    & $VENV_PY -m web.app
}
exit $LASTEXITCODE