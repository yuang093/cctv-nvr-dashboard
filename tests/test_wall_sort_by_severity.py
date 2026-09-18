"""
tests/test_wall_sort_by_severity.py
====================================
預設排序：訊號中斷 → 無訊號 → 在線；同類內事件新→舊；在線按名稱 A→Z。

對齊 spec §5 排序規則。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_wall_cameras_with_snapshots


@pytest.fixture
def seeded_env():
    """4 cam：2 signal_lost（不同時間）/ 1 no_signal / 1 online。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr(
        {
            "id": "NVR-A",
            "name": "A",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    w.begin_scan_run("2026-07-29T00:00:00Z")
    # 命名故意亂序，驗證排序是依嚴重度而非名稱
    w.upsert_cameras(
        nvra,
        {
            "c_online": {"name": "Z線上", "connection_state": "CONNECTED"},
            "c_sl_old": {"name": "A舊訊號", "connection_state": "CONNECTED"},
            "c_sl_new": {"name": "B新訊號", "connection_state": "CONNECTED"},
            "c_ns": {"name": "C無訊", "connection_state": "LONG_FAILED"},
        },
    )
    w.insert_events(
        w._current_scan_run_id,
        nvra,
        [
            {
                "eventId": "e_old",
                "deviceId": "c_sl_old",
                "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
                "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
                "occurred_at": "2026-07-29T01:00:00Z",
            },
            {
                "eventId": "e_new",
                "deviceId": "c_sl_new",
                "eventTopics": ["DEVICE_TAMPERING"],
                "eventTopic": "DEVICE_TAMPERING",
                "occurred_at": "2026-07-29T05:00:00Z",
            },
            {
                "eventId": "e_ns",
                "deviceId": "c_ns",
                "eventTopics": ["DEVICE_LONG_FAILED"],
                "eventTopic": "DEVICE_LONG_FAILED",
                "occurred_at": "2026-07-29T03:00:00Z",
            },
        ],
    )
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    yield db_path
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_signal_lost_before_no_signal_and_online(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    cats = [r["category"] for r in rows]
    # 順序：signal_lost, signal_lost, no_signal, online
    assert cats == ["signal_lost", "signal_lost", "no_signal", "online"]


def test_signal_lost_sorted_by_event_time_desc(seeded_env):
    """同類（signal_lost）內：事件新→舊。"""
    rows = get_wall_cameras_with_snapshots(seeded_env)
    sl_rows = [r for r in rows if r["category"] == "signal_lost"]
    # e_new (05:00) 應在 e_old (01:00) 之前
    assert sl_rows[0]["device_id"] == "c_sl_new"
    assert sl_rows[1]["device_id"] == "c_sl_old"


def test_online_sorted_by_camera_name_asc(seeded_env):
    """online 沒事件時間 → cam 名稱 A→Z。"""
    rows = get_wall_cameras_with_snapshots(seeded_env)
    online = [r for r in rows if r["category"] == "online"]
    assert len(online) == 1
    assert online[0]["device_id"] == "c_online"
    assert online[0]["camera_name"] == "Z線上"


def test_no_signal_in_middle(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    cats = [r["category"] for r in rows]
    assert cats.index("no_signal") == 2  # 在第 3 位（index 2）
