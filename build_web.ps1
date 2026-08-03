# build_web.ps1
# =============
# PowerShell 包裝：用於在 PowerShell 環境中乾淨執行 PyInstaller。
# （PyInstaller 的 stdout/stderr 會直接穿透顯示，不被 PowerShell 截切）
#
# 用法（PowerShell）：
#   .\build_web.ps1
#
# 用法（CMD）：
#   build_web.bat   （直接呼叫 .bat 會被 PowerShell 錯誤解析）
#
# 輸出：dist\web\web.exe  (onedir)

$ErrorActionPreference = "Continue"
$PROJECT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VENV_PY = Join-Path $PROJECT_DIR "venv\Scripts\python.exe"

if (-not (Test-Path $VENV_PY)) {
    Write-Error "[FATAL] venv python not found: $VENV_PY"
    exit 1
}

Set-Location $PROJECT_DIR

Write-Host "[INFO] Building web.exe (onedir, console) ..." -Foreground Cyan

& $VENV_PY -m PyInstaller `
    --noconfirm `
    --clean `
    --name web `
    --console `
    --onedir `
    --add-data "web/templates;web/templates" `
    --add-data "web/static;web/static" `
    --add-data "nvr_config.json;." `
    --collect-all flask `
    --collect-all jinja2 `
    --collect-all click `
    --collect-all itsdangerous `
    --collect-all markupsafe `
    --collect-all werkzeug `
    --collect-all reportlab `
    --collect-all tzdata `
    --collect-all requests `
    --collect-all urllib3 `
    --collect-all certifi `
    --collect-all charset_normalizer `
    --collect-all idna `
    --hidden-import sqlite3 `
    --hidden-import hashlib `
    --hidden-import json `
    --hidden-import csv `
    web\app.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "[FATAL] PyInstaller build failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "[OK] Built: $PROJECT_DIR\dist\web\web.exe" -Foreground Green
Write-Host "[OK] Run it:  dist\web\web.exe"
Write-Host "[INFO] Default: http://0.0.0.0:8444 (LAN-friendly)"
