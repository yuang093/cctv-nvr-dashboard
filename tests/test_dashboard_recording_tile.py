"""
tests/test_dashboard_recording_tile.py
======================================
Dashboard 統計：錄影中 cam 數（recording_cameras）。

規則：每台 cam 算一次
- 沒未解事件 → 視為「在線/錄影中」
- 有未解事件 → 視為「異常/非錄影中」

簡化版：錄影中數 = total_cameras - pending_events_covered_cameras
（避免掃描所有 cam 狀態；這與「在線」磁磚採同源）
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_overall_stats


@pytest.fixture
def db_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "c1": {"name": "cam1", "connection_state": "CONNECTED"},
        "c2": {"name": "cam2", "connection_state": "CONNECTED"},
        "c3": {"name": "cam3", "connection_state": "CONNECTED"},
    })
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "c2",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-29T00:00:00Z",
    }])
    w.finish_scan_run(rid, finished_at="2026-07-29T00:01:00Z", status="partial",
                      stats={"total_cameras": 3, "abnormal_cameras": 1,
                             "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    w.close()
    yield db_path
    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_stats_includes_recording_cameras(db_env):
    """get_overall_stats 應回 recording_cameras 欄位。"""
    stats = get_overall_stats(db_env)
    assert "recording_cameras" in stats
    assert stats["recording_cameras"] == 2  # c1, c3 → c2 有未解事件


def test_stats_recording_cameras_zero_when_no_cameras(db_env):
    """沒 cam → recording_cameras = 0。"""
    # 清 cam：直接用新 DB（避免破壞 fixture）
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db2 = f.name
    try:
        w = SqliteWriter(db2)
        w.upsert_nvr({"id": "X", "name": "X", "host": "1.1.1.1", "port": 8443,
                      "username": "u", "password": "p"})
        w.close()
        stats = get_overall_stats(db2)
        assert stats["recording_cameras"] == 0
    finally:
        Path(db2).unlink(missing_ok=True)
