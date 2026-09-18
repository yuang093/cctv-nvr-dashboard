"""
tests/test_list_cameras_for_nvr_ghost_filter.py
================================================
2026-07-31（Task A）：/timeline ghost 過濾掃描。

發現：`web.db.list_cameras_for_nvr`（給 8555 clip Web UI 抓 snapshot 用）
沒過濾 `is_ghost=1` → 會列到 NVR 已不管理的 cam，
前端送 Media API 時會 404。

修法：加 `AND is_ghost = 0` 到 query WHERE。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import list_cameras_for_nvr


@pytest.fixture
def ghost_nvr_app():
    """1 NVR，3 cam（d1 大門, d2 後門, d3 ghost）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr = w.upsert_nvr(
        {
            "id": "NVR-X",
            "name": "X 分店",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    rid = w.begin_scan_run("2026-07-31T06:00:00Z")
    w.upsert_cameras(
        nvr,
        {
            "d1": {"name": "大門", "ip_address": "10.0.0.10:443"},
            "d2": {"name": "後門", "ip_address": "10.0.0.11:443"},
            "d3": {"name": "ghost cam"},
        },
    )
    w.finish_scan_run(
        rid,
        finished_at="2026-07-31T06:00:01Z",
        status="success",
        stats={
            "total_cameras": 3,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    # 第二次 scan：d3 消失 → mark_ghost
    rid2 = w.begin_scan_run("2026-07-31T06:01:00Z")
    w.upsert_cameras(nvr, {"d1": {"name": "大門"}, "d2": {"name": "後門"}})
    w.mark_ghost_cameras(nvr, ["d1", "d2"])
    w.finish_scan_run(
        rid2,
        finished_at="2026-07-31T06:01:01Z",
        status="success",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    yield db_path, w, nvr
    del w
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 1. /timeline 跟 clip UI 不應列出 ghost cam ===
def test_list_cameras_for_nvr_excludes_ghost(ghost_nvr_app):
    """NVR 內 cam 列表不應包含 ghost（送進 Media API 會 404）。"""
    db_path, _w, nvr = ghost_nvr_app
    cams = list_cameras_for_nvr(db_path, nvr)
    device_ids = [c["device_id"] for c in cams]
    names = [c["name"] for c in cams]
    assert "d1" in device_ids
    assert "d2" in device_ids
    assert (
        "d3" not in device_ids
    ), "ghost cam 不應列在 NVR cam 清單（給 8555 clip UI 用）"
    assert "大門" in names
    assert "後門" in names
    assert "ghost cam" not in names
