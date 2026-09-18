"""
tests/test_ghost_cameras.py
============================
2026-07-30：/wall 顯示 cam3 ghost 的 bug。

場景：NVR 重啟時短暫看到一台 cam（rtsp://stream），之後消失。
DB 內 `cameras.last_seen_at` 停在消失前的時間，
但 `get_wall_cameras` 沒過濾 → /wall 仍顯示這台 cam 為 placeholder。

修法：
  1. `cameras` 表加 `is_ghost INTEGER NOT NULL DEFAULT 0` 欄位
  2. `SqliteWriter.mark_ghost_cameras(nvr_id, active_device_ids)`：
     - 沒在 active_device_ids 內的 cam → is_ghost = 1
     - active_device_ids 內的 cam → is_ghost = 0
  3. `get_wall_cameras` 與 `get_wall_cameras_with_snapshots` 預設過濾 `is_ghost = 0`
  4. /abnormal 也要過濾 ghost
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_wall_cameras, get_wall_cameras_with_snapshots


@pytest.fixture
def ghost_app():
    """3 台 cam：d1 (alive), d2 (alive), d3 (ghost)。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr = w.upsert_nvr(
        {
            "id": "NVR-A",
            "name": "A 分店",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    rid = w.begin_scan_run("2026-07-30T06:00:00Z")
    # 第一次 scan：3 台全看到
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
        finished_at="2026-07-30T06:00:01Z",
        status="success",
        stats={
            "total_cameras": 3,
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


# === 1. mark_ghost_cameras 標記消失的 cam 為 ghost ===
def test_mark_ghost_cameras_marks_missing_as_ghost(ghost_app):
    """第二次 scan 只看到 d1, d2。
    d3 沒看到 → 應被 mark 為 is_ghost = 1。
    d1, d2 仍有 → 應為 is_ghost = 0。
    """
    db_path, w, nvr = ghost_app
    rid2 = w.begin_scan_run("2026-07-30T06:01:00Z")
    w.upsert_cameras(nvr, {"d1": {"name": "大門"}, "d2": {"name": "後門"}})
    w.mark_ghost_cameras(nvr, ["d1", "d2"])
    w.finish_scan_run(
        rid2,
        finished_at="2026-07-30T06:01:01Z",
        status="success",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    import sqlite3

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = {
        r["device_id"]: r["is_ghost"]
        for r in con.execute(
            "SELECT device_id, is_ghost FROM cameras WHERE nvr_id = ?", (nvr,)
        )
    }
    con.close()
    assert rows["d1"] == 0
    assert rows["d2"] == 0
    assert rows["d3"] == 1, "d3 沒在這次 scan 內，應被標 ghost"


# === 2. /wall 預設過濾 ghost ===
def test_wall_filter_ghost_cameras(ghost_app):
    """d3 ghost 預設不出現在 /wall。"""
    db_path, w, nvr = ghost_app
    # 標 d3 為 ghost
    rid2 = w.begin_scan_run("2026-07-30T06:01:00Z")
    w.upsert_cameras(nvr, {"d1": {"name": "大門"}, "d2": {"name": "後門"}})
    w.mark_ghost_cameras(nvr, ["d1", "d2"])
    w.finish_scan_run(
        rid2,
        finished_at="2026-07-30T06:01:01Z",
        status="success",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    cams = get_wall_cameras(db_path, filter_kind="all")
    names = [c["camera_name"] for c in cams]
    assert "大門" in names
    assert "後門" in names
    assert "ghost cam" not in names, "ghost cam 預設不出現在 /wall"


# === 3. ghost cam 復活 → is_ghost 自動歸 0 ===
def test_ghost_camera_reappears_clears_flag(ghost_app):
    """d3 被標 ghost 後，NVR 再次看到 → 自動 is_ghost = 0。"""
    db_path, w, nvr = ghost_app
    # 第 2 次 scan：d3 消失
    rid2 = w.begin_scan_run("2026-07-30T06:01:00Z")
    w.upsert_cameras(nvr, {"d1": {"name": "大門"}, "d2": {"name": "後門"}})
    w.mark_ghost_cameras(nvr, ["d1", "d2"])
    w.finish_scan_run(
        rid2,
        finished_at="2026-07-30T06:01:01Z",
        status="success",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )
    # 第 3 次 scan：d3 又回來
    rid3 = w.begin_scan_run("2026-07-30T06:02:00Z")
    w.upsert_cameras(
        nvr,
        {
            "d1": {"name": "大門"},
            "d2": {"name": "後門"},
            "d3": {"name": "ghost cam"},
        },
    )
    w.mark_ghost_cameras(nvr, ["d1", "d2", "d3"])
    w.finish_scan_run(
        rid3,
        finished_at="2026-07-30T06:02:01Z",
        status="success",
        stats={
            "total_cameras": 3,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    import sqlite3

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT is_ghost FROM cameras WHERE nvr_id = ? AND device_id = ?",
        (nvr, "d3"),
    ).fetchone()
    con.close()
    assert row["is_ghost"] == 0, "d3 復活後應取消 ghost 標記"


# === 4. /wall 含縮圖版也過濾 ghost ===
def test_wall_with_snapshots_filter_ghost(ghost_app):
    """get_wall_cameras_with_snapshots 也過濾 ghost。"""
    db_path, w, nvr = ghost_app
    rid2 = w.begin_scan_run("2026-07-30T06:01:00Z")
    w.upsert_cameras(nvr, {"d1": {"name": "大門"}, "d2": {"name": "後門"}})
    w.mark_ghost_cameras(nvr, ["d1", "d2"])
    w.finish_scan_run(
        rid2,
        finished_at="2026-07-30T06:01:01Z",
        status="success",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    cams = get_wall_cameras_with_snapshots(db_path, filter_kind="all")
    names = [c["camera_name"] for c in cams]
    assert "ghost cam" not in names
