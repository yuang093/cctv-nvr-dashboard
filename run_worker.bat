@echo off
REM run_worker.bat
REM ==============
REM Windows 工作排程器入口腳本（給 taskschd.msc 呼叫）。
REM
REM 行為：
REM   - cd 到專案目錄
REM   - 啟用 venv
REM   - 執行 nvr_scanner.py，輸出附加寫到 logs\nvr_scanner.log
REM   - exit code 透傳
REM
REM 自訂區：修改下方的 PROJECT_DIR 與 LOG_DIR

REM === 自訂區（依部署環境修改） ===
set "PROJECT_DIR=C:\nvr"
set "LOG_DIR=%PROJECT_DIR%\logs"

REM === 主流程 ===
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [FATAL] 找不到專案目錄：%PROJECT_DIR% 1>&2
    exit /b 2
)

if not exist "venv\Scripts\activate.bat" (
    echo [FATAL] 找不到 venv：%PROJECT_DIR%\venv\Scripts\activate.bat 1>&2
    echo 請先建立虛擬環境：python -m venv venv 1>&2
    exit /b 3
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [FATAL] venv 啟用失敗 1>&2
    exit /b 4
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

python nvr_scanner.py >> "%LOG_DIR%\nvr_scanner.log" 2>&1
set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo [%date% %time%] run_worker.bat exit_code=%EXIT_CODE% >> "%LOG_DIR%\nvr_scanner.log"
)

exit /b %EXIT_CODE%
