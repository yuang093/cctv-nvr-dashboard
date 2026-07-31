"""
tests/test_abnormal_ghost_filtering.py
======================================
/abnormal 異常事件頁應排除 ghost cam（is_ghost=1）。

根因：get_abnormal_cameras_grouped 用 INNER JOIN cameras 沒過濾 is_ghost，
導致 ghost cam 觸發的事件（如 STATE_DISCONNECTED）也計入異常清單。

設計：
- 修前 → /abnormal 顯示 ghost cam rtsp://（其實 NVR 已不再管理）
- 修後 → 只列仍被管理的 cam
"""
from __future__ import annotations

import gc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_abnormal_cameras_grouped


@pytest.fixture
def abnormal_ghost_env():
    """2 台真 cam + 1 ghost cam，全部都有 open event。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr({
        "id": "NVR-A", "name": "real nvr", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(nvr_int, {
        "real-1": {"name": "cam1", "connection_state": "DISCONNECTED"},
        "real-2": {"name": "cam2", "connection_state": "CONNECTED"},
        "ghost-1": {"name": "rtsp://192.168.133.105:554/x",
                    "connection_state": "DISCONNECTED"},
    })
    w.insert_events(rid, nvr_int, [
        {"eventId": "e1", "deviceId": "real-1",
         "eventTopics": ["STATE_DISCONNECTED"], "eventTopic": "STATE_DISCONNECTED",
         "occurred_at": "2026-07-30T00:00:00Z"},
        {"eventId": "e2", "deviceId": "ghost-1",
         "eventTopics": ["STATE_DISCONNECTED"], "eventTopic": "STATE_DISCONNECTED",
         "occurred_at": "2026-07-30T00:00:00Z"},
    ])
    w.finish_scan_run(
        rid, finished_at="2026-07-30T00:01:00Z", status="complete",
        stats={"total_cameras": 3, "abnormal_cameras": 2,
               "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0},
    )
    w.close()

    # 標記 ghost（模擬 mark_ghost_cameras 跑過）
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


# === 1. /abnormal 排除 ghost cam ===
def test_abnormal_excludes_ghost_cameras(abnormal_ghost_env):
    """異常清單不應包含 ghost cam rtsp:// 的事件。"""
    groups = get_abnormal_cameras_grouped(abnormal_ghost_env)
    keys = {(g["device_id"]) for g in groups}
    assert "ghost-1" not in keys, (
        f"ghost cam 不應在 /abnormal；實際：{keys}"
    )
    # 真 cam 仍在
    assert "real-1" in keys
    # cam2 沒事件 → 不應該在清單
    assert "real-2" not in keys
    # 修前：2 個 group（real-1 + ghost-1）；修後：1 個 group（real-1）
    assert len(groups) == 1, (
        f"預期 1 個 group (real-1)，實際 {len(groups)} 個：{keys}"
    )
