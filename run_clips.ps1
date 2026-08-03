# run_clips.ps1
# =============
# Windows PowerShell entry script for Phase 2.7 Clip Web UI (Flask, port 8555).
#
# 給另一部門同事用，與既有 8444 Web UI 完全分離。
#
# Usage (foreground, development / smoke test):
#   .\run_clips.ps1
#
# Usage (Task Scheduler "Action" tab):
#   Program: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
#   Args:    -ExecutionPolicy Bypass -File "C:\nvr\run_clips.ps1"
#
# Environment variables (optional, override defaults below):
#   NVR_CLIPS_HOST  default 0.0.0.0     (LAN-friendly; 127.0.0.1 = 本機 only)
#   NVR_CLIPS_PORT  default 8555        (user-specified, avoids NVR 8443 / 8444)
#   NVR_DB_PATH     default <project_dir>\nvr_scan.db
#   NVR_CLIPS_CLIENT "mock" = MockMediaClient (testing only)

$ErrorActionPreference = "Continue"

# === Customizable section (edit for your deployment) ===
$PROJECT_DIR = (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$VENV_PY     = Join-Path $PROJECT_DIR "venv\Scripts\python.exe"

$ClipsHost   = if ($env:NVR_CLIPS_HOST) { $env:NVR_CLIPS_HOST } else { "0.0.0.0" }
$ClipsPort   = if ($env:NVR_CLIPS_PORT) { $env:NVR_CLIPS_PORT } else { "8555" }
$DbPath      = if ($env:NVR_DB_PATH)    { $env:NVR_DB_PATH    } else { Join-Path $PROJECT_DIR "nvr_scan.db" }

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
$existing = Get-NetTCPConnection -LocalPort $ClipsPort -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    foreach ($conn in $existing) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq "python") {
            Write-Host "[INFO] Killing leftover Flask on port $ClipsPort (PID $($proc.Id), started $($proc.StartTime))"
            Stop-Process -Id $proc.Id -Force
        }
    }
    Start-Sleep -Milliseconds 500
}

# Export for web.clips_app to consume
$env:NVR_CLIPS_HOST = $ClipsHost
$env:NVR_CLIPS_PORT = $ClipsPort
$env:NVR_DB_PATH    = $DbPath

Write-Host "[INFO] Starting NVR Clip Web UI at http://${ClipsHost}:${ClipsPort}"
Write-Host "[INFO] DB: $DbPath"
Write-Host "[INFO] Press Ctrl+C to stop"

# Foreground run; ctrl+c kills the Flask process
& $VENV_PY -m web.clips_app
exit $LASTEXITCODE
