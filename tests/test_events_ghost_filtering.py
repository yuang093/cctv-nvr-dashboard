"""
tests/test_events_ghost_filtering.py
====================================
/events 事件列表頁應排除 ghost cam 觸發的事件（is_ghost=1）。

根因：get_events_filtered 用 LEFT JOIN cameras 沒過濾 is_ghost，
導致 ghost cam 觸發的事件仍顯示在 /events（即 NVR 已不再管理的 cam）。

設計：
- 修前 → /events 含 ghost cam 的事件
- 修後 → 只列「仍被管理」cam 的事件
- 保留 LEFT JOIN → 允許 cam row 還沒建的合法場景
"""
from __future__ import annotations

import gc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_events_filtered


@pytest.fixture
def events_ghost_env():
    """2 台真 cam + 1 ghost cam，全部都有事件。"""
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


# === 1. /events 排除 ghost cam 事件，但保留真 cam ===
def test_events_excludes_ghost_but_keeps_real(events_ghost_env):
    """get_events_filtered 應過濾 ghost cam 的事件。"""
    # 24h 內、status=all
    rows = get_events_filtered(events_ghost_env, hours=24, status="all")
    device_ids = {r["device_id"] for r in rows}
    assert "ghost-1" not in device_ids, (
        f"ghost cam 事件不應在 /events；實際：{device_ids}"
    )
    assert "real-1" in device_ids, "真 cam 事件應該還在"
    # 修前：2 個 events；修後：1 個 event
    assert len(rows) == 1
