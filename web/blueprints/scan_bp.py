"""Week 6 #017 — scan_bp。

URL prefix: `/scan`, `/dashboard/refresh-completeness`
路由（4 條）：
    `/scan`                                       POST  背景掃描觸發
    `/scan/status`                                GET   掃描狀態輪詢
    `/dashboard/refresh-completeness`             POST  timeline 重整觸發
    `/dashboard/refresh-completeness/status`      GET   timeline 狀態

Module-level state（process 級全域、跨 thread 共用）：
- `_scan_state` / `_scan_lock`  — 背景 thread 跑 batch_scan 進度
- `_timeline_state` / `_timeline_lock`  — 24h timeline 重整進度
- background workers：`_run_scan_in_background`、`_run_timeline_refresh`

設計：Plan §D2 — 保留 module-level 全域、不綁進 factory。
`_run_scan_*` 與 `_reset_scan_state` / `_finish_scan_state` helper 仍保留在 web/app.py，
此 bp 從 app.py import 進來用，避免重複定義。
"""
from __future__ import annotations

from typing import cast

from flasgger import swag_from

import threading
import time

from flask import Blueprint, current_app, jsonify, request

from web import db as webdb
from web.app import (
    _reset_scan_state,
    _reset_timeline_state,
    _finish_scan_state,
    _finish_timeline_state,
    _archive_current_report,
)

# Stage A：state 仍保留在 web/app.py module 級；本 bp 僅 register 路由呼叫。
# Stage B 後再考慮把 _scan_state / _timeline_state 也搬到此處（Plan §D2）。

scan_bp = Blueprint("scan", __name__)


def _db_path() -> str:
    return cast(str, current_app.config["DB_PATH"])


@scan_bp.route("/scan", methods=["POST"])
@swag_from("web/openapi/dashboard/scan_run.yml")
def scan_trigger():
    """啟動背景 thread 跑 batch_scan。

    若已在跑，回 409 conflict。
    同步回 202 + scan 起始資訊；前端輪詢 /scan/status 看進度。
    """
    from db.sqlite_writer import acquire_scan_lock
    from web.app import _scan_lock, _scan_state, _run_scan_in_background

    with _scan_lock:
        if _scan_state["running"]:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "已有掃描在進行中",
                        "started_at": _scan_state["started_at"],
                    }
                ),
                409,
            )

        db_path = _db_path()
        if not acquire_scan_lock(db_path, timeout=0):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "已有掃描在進行中（DB 內 status='running'，可能是 cron 或其他 process）",
                    }
                ),
                409,
            )

        try:
            enabled_nvrs = webdb.list_enabled_nvrs(db_path)
            enabled_count = len(enabled_nvrs)
        except Exception:
            enabled_count = 0
        _reset_scan_state(total_nvrs=enabled_count)

        thread = threading.Thread(
            target=_run_scan_in_background,
            args=(current_app._get_current_object(), db_path),
            daemon=True,
            name="nvr-scan",
        )
        thread.start()

    return (
        jsonify(
            {
                "ok": True,
                "started_at": _scan_state["started_at"],
                "total_nvrs": enabled_count,
            }
        ),
        202,
    )


@scan_bp.route("/scan/status")
@swag_from("web/openapi/dashboard/scan_status.yml")
def scan_status():
    """查目前 scan 狀態（給前端 polling）。"""
    from web.app import _scan_lock, _scan_state

    with _scan_lock:
        return jsonify(dict(_scan_state))


@scan_bp.route("/dashboard/refresh-completeness", methods=["POST"])
@swag_from("web/openapi/dashboard/scan_refresh_completeness.yml")
def refresh_completeness_trigger():
    """啟動 background thread 跑 24h timeline 收集。

    已在跑 → 409 conflict。同步回 202 + 啟動時間。
    """
    from web.app import _timeline_lock, _timeline_state, _run_timeline_refresh

    with _timeline_lock:
        if _timeline_state["running"]:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "已有完整率重整在進行中",
                        "started_at": _timeline_state["started_at"],
                    }
                ),
                409,
            )
        _reset_timeline_state()

    db_path = _db_path()
    thread = threading.Thread(
        target=_run_timeline_refresh,
        args=(current_app._get_current_object(), db_path),
        daemon=True,
        name="timeline-refresh",
    )
    thread.start()

    return (
        jsonify(
            {
                "ok": True,
                "started_at": _timeline_state["started_at"],
            }
        ),
        202,
    )


@scan_bp.route("/dashboard/refresh-completeness/status")
@swag_from("web/openapi/dashboard/scan_refresh_completeness_status.yml")
def refresh_completeness_status():
    """查目前 timeline refresh 進度（給前端 polling）。"""
    from web.app import _timeline_lock, _timeline_state

    with _timeline_lock:
        return jsonify(dict(_timeline_state))
