#!/usr/bin/env bash
# run_worker.sh
# =============
# Linux / macOS 排程器入口腳本（給 cron 或 systemd timer 呼叫）。
#
# 行為：
#   - cd 到專案根目錄
#   - 直接用 venv 內的 python（不需 source activate）
#   - 全部輸出（stdout + stderr）附加寫到 LOG_FILE
#   - exit code 透傳到 cron（cron 會記錄，可監控失敗）
#
# 用法（crontab -e）：
#   */15 * * * * /opt/nvr/run_worker.sh
#
# 自訂環境：
#   - 修改下方的 PROJECT_DIR / LOG_FILE
#   - 加上 LOG_ROTATE 設定（見 deployment_backup.md §3.1）

set -u  # 未設定變數直接失敗；不用 -e 因為 python 例外已由 nvr_scanner.py 處理

# === 自訂區（依部署環境修改） ===
PROJECT_DIR="/opt/nvr"
VENV_PY="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="/var/log"
LOG_FILE="${LOG_DIR}/nvr_scanner.log"

# === 主流程 ===
cd "$PROJECT_DIR" || {
    echo "[FATAL] 找不到專案目錄：$PROJECT_DIR" >&2
    exit 2
}

# 確認 venv 內 python 存在
if [ ! -x "$VENV_PY" ]; then
    echo "[FATAL] venv python 不可執行：$VENV_PY" >&2
    echo "請先建立虛擬環境：python -m venv ${PROJECT_DIR}/venv" >&2
    exit 3
fi

# 確保 log 目錄存在
mkdir -p "$LOG_DIR"

# 執行掃描（stdin 從 /dev/null 避免 cron 卡住）
"$VENV_PY" nvr_scanner.py < /dev/null >> "$LOG_FILE" 2>&1
exit_code=$?

# 簡易 failure log（給監控腳本 grep 用）
if [ $exit_code -ne 0 ]; then
    echo "[$(date -Iseconds)] run_worker.sh exit_code=$exit_code" >> "$LOG_FILE"
fi

exit $exit_code
