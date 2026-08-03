# run_worker.ps1
# ==============
# Windows PowerShell 工作排程器入口腳本（給「以 PowerShell 執行」動作用）。
#
# 用法（建立工作排程器的「動作」分頁）：
#   程式/指令碼：C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
#   引數：-ExecutionPolicy Bypass -File "C:\nvr\run_worker.ps1"
#
# 自訂區：修改下方的 $PROJECT_DIR 與 $LOG_DIR

# 設定錯誤處理：不要因非 0 exit code 中斷（要寫到 log）
$ErrorActionPreference = "Continue"

# === 自訂區（依部署環境修改） ===
$PROJECT_DIR = "C:\nvr"
$LOG_DIR = Join-Path $PROJECT_DIR "logs"
$VENV_ACTIVATE = Join-Path $PROJECT_DIR "venv\Scripts\Activate.ps1"

# === 主流程 ===
Set-Location $PROJECT_DIR
if ($LASTEXITCODE -ne 0) {
    Write-Error "[FATAL] 找不到專案目錄：$PROJECT_DIR"
    exit 2
}

if (-not (Test-Path $VENV_ACTIVATE)) {
    Write-Error "[FATAL] 找不到 venv：$VENV_ACTIVATE"
    Write-Error "請先建立虛擬環境：python -m venv venv"
    exit 3
}

# 啟用 venv
& $VENV_ACTIVATE
if ($LASTEXITCODE -ne 0) {
    Write-Error "[FATAL] venv 啟用失敗"
    exit 4
}

# 確保 log 目錄存在
if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR | Out-Null
}

$LOG_FILE = Join-Path $LOG_DIR "nvr_scanner.log"

# 執行掃描（Tee-Object 同時印到 stdout 與 log file）
python nvr_scanner.py 2>&1 | Tee-Object -FilePath $LOG_FILE -Append
$EXIT_CODE = $LASTEXITCODE

if ($EXIT_CODE -ne 0) {
    Add-Content -Path $LOG_FILE -Value "[$(Get-Date -Format 'o')] run_worker.ps1 exit_code=$EXIT_CODE"
}

exit $EXIT_CODE
