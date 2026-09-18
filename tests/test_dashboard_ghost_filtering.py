"""
tests/test_dashboard_ghost_filtering.py
=======================================
Dashboard 統計 + 缺錄排名 都應該排除 ghost cam（is_ghost=1）。

根因：get_overall_stats 與 get_top_missing_cameras 兩條 query 沒過濾 is_ghost，
導致 NVR 已不再管理的 cam（rtsp:// 之類的幽靈）也列入統計。

設計：
- 修前 → 攝影機總數 3（應只 2）、TOP5 有 3 筆（應只 2 筆）
- 修後 → 兩條 query 都加 is_ghost=0 過濾
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest
import sqlite3

from db.sqlite_writer import SqliteWriter
from web.db import get_overall_stats, get_top_missing_cameras


@pytest.fixture
def ghost_env():
    """建立 2 台真 cam + 1 台 ghost cam，每台都有 recording_status。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr(
        {
            "id": "NVR-A",
            "name": "real nvr",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    rid = w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(
        nvr_int,
        {
            "real-1": {"name": "cam1", "connection_state": "CONNECTED"},
            "real-2": {"name": "cam2", "connection_state": "CONNECTED"},
            "ghost-1": {
                "name": "rtsp://192.168.133.105:554/rtsp/x",
                "connection_state": "DISCONNECTED",
            },
        },
    )
    # 為每台 cam 寫一筆 recording_status
    for cam in ("real-1", "real-2", "ghost-1"):
        w.upsert_recording_status(
            nvr_int,
            cam,
            window_start="2026-07-29T00:00:00Z",
            window_end="2026-07-30T00:00:00Z",
            completeness=0.0 if cam == "ghost-1" else 0.8,
            missing_seconds=86400.0 if cam == "ghost-1" else 17280.0,
        )
    w.finish_scan_run(
        rid,
        finished_at="2026-07-30T00:01:00Z",
        status="complete",
        stats={
            "total_cameras": 3,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )
    w.close()

    # 直接 SQL UPDATE 標記 ghost（模擬 mark_ghost_cameras 跑過）
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE cameras SET is_ghost = 1 WHERE device_id = 'ghost-1'")
    conn.commit()
    conn.close()

    yield db_path

    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 1. get_overall_stats 排除 ghost cam ===
def test_overall_stats_excludes_ghost_cameras(ghost_env):
    """攝影機總數只算 is_ghost=0 — 不應是 3 應是 2。"""
    stats = get_overall_stats(ghost_env)
    # 修前：3（全部 cameras）；修後：2（過濾 ghost）
    assert (
        stats["total_cameras"] == 2
    ), f"expected 2 (excluding ghost), got {stats['total_cameras']}"


# === 2. get_top_missing_cameras 排除 ghost cam ===
def test_top_missing_excludes_ghost_cameras(ghost_env):
    """TOP5 不應包含 ghost cam（pct=0% 那筆）。"""
    rows = get_top_missing_cameras(ghost_env, limit=5)
    cam_ids = {r["camera_id"] for r in rows}
    assert "ghost-1" not in cam_ids, f"ghost cam 不應在 TOP5；實際：{cam_ids}"
    # 真 cam 仍在
    assert "real-1" in cam_ids
    assert "real-2" in cam_ids
