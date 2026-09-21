"""Week 6 #018 — clips_coverage_bp。

URL prefix: `/clips/coverage`
路由（2 條）：
    `/clips/coverage`        GET  熱區頁（coverage.html）
    `/clips/coverage/data`   GET  熱區 JSON（Spec F 含 NVR MPD 拉取）

依賴：
- web.coverage.fetch_coverage_from_nvr
- nvr_scanner.AvigilonScanner
- web.db.get_nvr / list_cameras_for_nvr
- web.clips_helpers.login_nvr / build_nvr_config / get_db_path
"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, render_template, request
from nvr_scanner import get_credential

from web import db as webdb
from web.clips_helpers import build_nvr_config, get_db_path, login_nvr
from web.coverage import fetch_coverage_from_nvr

logger = logging.getLogger("nvr.clips")

coverage_bp = Blueprint("clips_coverage", __name__, url_prefix="/clips/coverage")


@coverage_bp.route("")
def coverage():
    """錄影覆蓋熱區頁面（給 1 台 NVR 看所有 cam 24h 錄影時間軸）。"""
    return render_template("coverage.html")


@coverage_bp.route("/data")
def coverage_data():
    """JSON API：回傳 1 台 NVR 所有 cam 的 timeline 資料。

    Spec F 合規（2026-08-04）：
    1. 認證 env 顯式檢查（不允許 fallback 到互動 prompt）
    2. ISO 8601 字串 parse 成 datetime 後比較
    3. NVR 連線失敗嚴格回 502（不再 silent fallback）
    """
    import os
    from datetime import datetime

    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    if not user_nonce or not user_key:
        return (
            jsonify(
                {
                    "error": "伺服器未設定 AVIGILON_USER_NONCE / AVIGILON_USER_KEY（請檢查 .env）"
                }
            ),
            500,
        )

    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    if not internal_id:
        return jsonify({"error": "缺少 nvr_id"}), 400

    start_iso = request.args.get("start", "")
    end_iso = request.args.get("end", "")
    if not start_iso or not end_iso:
        return jsonify({"error": "缺少 start / end"}), 400
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return jsonify({"error": "start / end 必須是 ISO 8601 格式"}), 400
    if end_dt <= start_dt:
        return jsonify({"error": "end 必須大於 start"}), 400

    db_path = get_db_path()
    nvr_row = webdb.get_nvr(db_path, internal_id)
    if nvr_row is None:
        return jsonify({"error": f"找不到 NVR id={internal_id}"}), 404

    cams = webdb.list_cameras_for_nvr(db_path, internal_id)
    if not cams:
        return jsonify({"error": "該 NVR 沒有 cam"}), 404

    try:
        session_token = login_nvr(nvr_row)
        from nvr_scanner import AvigilonScanner

        scanner = AvigilonScanner(
            build_nvr_config(nvr_row),
            user_nonce=get_credential(
                "AVIGILON_USER_NONCE", "AVIGILON_USER_NONCE", hide=False
            ),
            user_key=get_credential(
                "AVIGILON_USER_KEY", "AVIGILON_USER_KEY", hide=True
            ),
            verify_ssl=bool(nvr_row.get("verify_ssl", 0)),
        )
        scanner._session_token = session_token

        def fetch_one(cam_id: str, s: str, e: str) -> dict:
            return scanner.get_timeline(cam_id, from_iso=s, to_iso=e)

        out = fetch_coverage_from_nvr(
            nvr={
                "host": nvr_row["host"],
                "port": nvr_row["port"],
                "nvr_id": nvr_row.get("nvr_id", ""),
            },
            cameras=[
                {
                    "device_id": c["device_id"],
                    "camera_name": c.get("name", c["device_id"]),
                }
                for c in cams
            ],
            start_iso=start_iso,
            end_iso=end_iso,
            timeline_fetcher=fetch_one,
        )
    except Exception as e:
        logger.error("/clips/coverage/data 抓取 NVR 失敗 nvr_id=%d: %s", internal_id, e)
        return jsonify({"error": f"抓取 NVR 失敗: {e}"}), 502

    return jsonify(out)
