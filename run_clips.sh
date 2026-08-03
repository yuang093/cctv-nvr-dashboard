#!/usr/bin/env bash
# run_clips.sh
# ============
# Linux / macOS 啟動 Phase 2.7 影片片段調閱 Web UI（Flask, port 8555）。
#
# 給另一部門同事用，與既有 8444 Web UI 完全分離。
#
# 用法：
#   ./run_clips.sh                            # 預設 0.0.0.0:8555
#   NVR_CLIPS_HOST=127.0.0.1 ./run_clips.sh  # 只本機可連
#   NVR_CLIPS_PORT=9090 ./run_clips.sh       # 改 port
#
# 環境變數（選擇性）：
#   NVR_CLIPS_HOST  預設 0.0.0.0
#   NVR_CLIPS_PORT  預設 8555
#   NVR_DB_PATH     預設 <project_dir>/nvr_scan.db
#   NVR_CLIPS_CLIENT "mock" = 走 MockMediaClient（測試用）

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="${PROJECT_DIR}/venv/bin/python"
HOST="${NVR_CLIPS_HOST:-0.0.0.0}"
PORT="${NVR_CLIPS_PORT:-8555}"
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

export NVR_CLIPS_HOST="$HOST"
export NVR_CLIPS_PORT="$PORT"
export NVR_DB_PATH="$DB_PATH"

echo "[INFO] Starting NVR Clip Web UI at http://${HOST}:${PORT}"
echo "[INFO] DB: ${DB_PATH}"
echo "[INFO] 按 Ctrl+C 停止"

exec "$VENV_PY" -m web.clips_app
