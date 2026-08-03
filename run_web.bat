@echo off
REM run_web.bat
REM ===========
REM Windows 啟動 v2 Web UI（Flask）。
REM
REM 用法：直接雙擊，或在 cmd 執行 run_web.bat
REM 環境變數（可選）：
REM   NVR_WEB_HOST  預設 0.0.0.0（LAN 友善；設 127.0.0.1 = 只本機可連）
REM   NVR_WEB_PORT  預設 8444（避開 NVR 8443）
REM   NVR_DB_PATH   預設 .\nvr_scan.db
REM
REM 對外暴露時務必加 reverse proxy (nginx / caddy) + HTTPS

set "PROJECT_DIR=%~dp0"
set "VENV_PY=%PROJECT_DIR%venv\Scripts\python.exe"
if not "%NVR_WEB_HOST%"=="" goto host_set
    set "NVR_WEB_HOST=0.0.0.0"
:host_set
if not "%NVR_WEB_PORT%"=="" goto port_set
    set "NVR_WEB_PORT=8444"
:port_set
if not "%NVR_DB_PATH%"=="" goto db_set
    set "NVR_DB_PATH=%PROJECT_DIR%nvr_scan.db"
:db_set

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [FATAL] 找不到專案目錄：%PROJECT_DIR% 1>&2
    exit /b 2
)

if not exist "%VENV_PY%" (
    echo [FATAL] 找不到 venv python：%VENV_PY% 1>&2
    echo 請先：python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements-web.txt 1>&2
    exit /b 3
)

echo [INFO] Starting NVR Web UI at http://%NVR_WEB_HOST%:%NVR_WEB_PORT%
echo [INFO] DB: %NVR_DB_PATH%
echo 按 Ctrl+C 停止

"%VENV_PY%" -m web.app
