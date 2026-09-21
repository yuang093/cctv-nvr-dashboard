"""Week 6 #017 — scan_bp。

URL prefix: `/scan`, `/dashboard/refresh-completeness`
路由（4 條）：
    `/scan`                                       POST  背景掃描觸發
    `/scan/status`                                GET   掃描狀態輪詢
    `/dashboard/refresh-completeness`             POST  timeline 重整觸發
    `/dashboard/refresh-completeness/status`      GET   timeline 狀態

Module-level state（Phase 2.6+）：
- `_scan_state` / `_scan_lock`  — 跨 thread 共用
- `_timeline_state` / `_timeline_lock`  — 跨 thread 共用
- 背景 worker functions：`_run_scan_in_background`、`_run_timeline_refresh`

設計：保留 module-level 全域（Plan §D2 拍板），跨 thread 行為零變。
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint

scan_bp = Blueprint("scan", __name__)

# === Module-level scan state（process 級全域；跨 thread 可見）===
_scan_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "run_id": None,
    "total_nvrs": 0,
    "ok_nvrs": 0,
    "failed_nvrs": 0,
    "abnormal_cameras": 0,
    "error": None,
}
_scan_lock = threading.RLock()

# === Module-level timeline refresh state（process 級全域）===
_timeline_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "checked": 0,
    "written": 0,
    "errors_count": 0,
    "error": None,
}
_timeline_lock = threading.RLock()


def reset_scan_state(total_nvrs: int | None = None) -> None:
    """重置 scan 狀態（給 route + background worker 共用）。"""
    with _scan_lock:
        _scan_state.update(
            {
                "running": True,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "finished_at": None,
                "run_id": None,
                "ok_nvrs": 0,
                "failed_nvrs": 0,
                "abnormal_cameras": 0,
                "error": None,
            }
        )
        if total_nvrs is not None:
            _scan_state["total_nvrs"] = total_nvrs


def finish_scan_state(success: bool, **kwargs) -> None:
    with _scan_lock:
        _scan_state["running"] = False
        _scan_state["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        _scan_state.update(kwargs)
        if not success and "error" not in kwargs:
            _scan_state["error"] = "scan failed"


def reset_timeline_state() -> None:
    """重置 timeline refresh 狀態。"""
    with _timeline_lock:
        _timeline_state.update(
            {
                "running": True,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "finished_at": None,
                "checked": 0,
                "written": 0,
                "errors_count": 0,
                "error": None,
            }
        )


def finish_timeline_state(success: bool, **kwargs) -> None:
    with _timeline_lock:
        _timeline_state["running"] = False
        _timeline_state["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        _timeline_state.update(kwargs)
        if not success and "error" not in kwargs:
            _timeline_state["error"] = "timeline refresh failed"
