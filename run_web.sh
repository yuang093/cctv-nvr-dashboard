#!/usr/bin/env bash
# run_web.sh
# ==========
# Linux / macOS 啟動 v2 Web UI（Flask）。
#
# 用法：
#   ./run_web.sh                              # 預設 0.0.0.0:5000（LAN 友善）
#   NVR_WEB_HOST=127.0.0.1 ./run_web.sh      # 只本機可連
#   NVR_WEB_PORT=8080 ./run_web.sh           # 改 port
#   NVR_WEB_HOST=0.0.0.0 ./run_web.sh        # ⚠️ 對外暴露務必加反向代理
#
# 排程 vs 服務：
#   - 開發/測試：直接前景跑，看 log
#   - 正式部署：用 systemd / supervisor / screen / tmux
#   - **不建議** 放 cron（cron 適合週期任務，Web 是常駐服務）

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="${PROJECT_DIR}/venv/bin/python"
HOST="${NVR_WEB_HOST:-127.0.0.1}"   # Day-0: 預設只綁本機,避免公網意外暴露
PORT="${NVR_WEB_PORT:-5000}"
DB_PATH="${NVR_DB_PATH:-${PROJECT_DIR}/nvr_scan.db}"

cd "$PROJECT_DIR" || {
    echo "[FATAL] 找不到專案目錄：$PROJECT_DIR" >&2
    exit 2
}

if [ ! -x "$VENV_PY" ]; then
    echo "[FATAL] venv python 不可執行：$VENV_PY" >&2
    echo "請先：python -m venv venv && source venv/bin/activate && pip install -r requirements-web.txt" >&2
    exit 3
fi

export NVR_WEB_HOST="$HOST"
export NVR_WEB_PORT="$PORT"
export NVR_DB_PATH="$DB_PATH"

# Week 5 #013：HTTPS 模式（NVR_HTTPS_ENABLED=1 → Werkzeug adhoc SSL）
HTTPS_FLAG=""
if [ "${NVR_HTTPS_ENABLED:-0}" == "1" ]; then
    HTTPS_FLAG="--https=adhoc"
    echo "[INFO] HTTPS mode: Werkzeug adhoc SSL（瀏覽器會警告自簽憑證）"
    echo "[INFO] Starting NVR Web UI at https://${HOST}:${PORT}"
else
    echo "[INFO] Starting NVR Web UI at http://${HOST}:${PORT}"
fi
echo "[INFO] DB: ${DB_PATH}"
echo "[INFO] 按 Ctrl+C 停止"

if [ -n "$HTTPS_FLAG" ]; then
    exec "$VENV_PY" -m web.app $HTTPS_FLAG
else
    exec "$VENV_PY" -m web.app
fi
