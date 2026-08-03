@echo off
REM run_clips.bat
REM =============
REM Windows 啟動 Phase 2.7 影片片段調閱 Flask（port 8555，給另一部門用）。
REM
REM 環境變數（可選）：
REM   NVR_CLIPS_HOST  預設 0.0.0.0（LAN 友善；設 127.0.0.1 = 只本機可連）
REM   NVR_CLIPS_PORT  預設 8555（用戶指定；避開 NVR 8443 / 既有 8444）
REM   NVR_DB_PATH     預設 .\nvr_scan.db
REM   NVR_CLIPS_CLIENT mock = 走 MockMediaClient（測試用）

set "PROJECT_DIR=%~dp0"
set "VENV_PY=%PROJECT_DIR%venv\Scripts\python.exe"
if not "%NVR_CLIPS_HOST%"=="" goto host_set
    set "NVR_CLIPS_HOST=0.0.0.0"
:host_set
if not "%NVR_CLIPS_PORT%"=="" goto port_set
    set "NVR_CLIPS_PORT=8555"
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

echo [INFO] Starting NVR Clip Web UI at http://%NVR_CLIPS_HOST%:%NVR_CLIPS_PORT%
echo [INFO] DB: %NVR_DB_PATH%
echo 按 Ctrl+C 停止

"%VENV_PY%" -m web.clips_app
