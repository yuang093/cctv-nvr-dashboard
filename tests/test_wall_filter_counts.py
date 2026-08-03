"""
tests/test_wall_filter_counts.py
================================
web.db.get_wall_filter_counts()：永遠回 4 類全 DB 計數（不受 ?filter= 影響）。

對齊 spec §3 計數來源：
  - 計數永遠是全 DB 統計
  - 即使 ?filter=online，計數仍顯示 all/online/signal_lost/no_signal 各 N
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_wall_filter_counts


@pytest.fixture
def seeded_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "c1": {"name": "cam1"},
        "c2": {"name": "cam2"},
        "c3": {"name": "cam3"},
        "c4": {"name": "cam4"},
        "c5": {"name": "cam5"},
    })
    # c1: signal_lost
    w.insert_events(w._current_scan_run_id, nvra, [{
        "eventId": "e1", "deviceId": "c1",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-29T01:00:00Z",
    }])
    # c2: no_signal
    w.insert_events(w._current_scan_run_id, nvra, [{
        "eventId": "e2", "deviceId": "c2",
        "eventTopics": ["DEVICE_LONG_FAILED"],
        "eventTopic": "DEVICE_LONG_FAILED",
        "occurred_at": "2026-07-29T02:00:00Z",
    }])
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    yield db_path
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_returns_4_keys(seeded_env):
    counts = get_wall_filter_counts(seeded_env)
    assert set(counts.keys()) == {"all", "online", "signal_lost", "no_signal"}


def test_all_equals_total_cameras(seeded_env):
    counts = get_wall_filter_counts(seeded_env)
    assert counts["all"] == 5


def test_signal_lost_count(seeded_env):
    counts = get_wall_filter_counts(seeded_env)
    assert counts["signal_lost"] == 1


def test_no_signal_count(seeded_env):
    counts = get_wall_filter_counts(seeded_env)
    assert counts["no_signal"] == 1


def test_online_count(seeded_env):
    """5 台 cam - 1 signal_lost - 1 no_signal = 3 online。"""
    counts = get_wall_filter_counts(seeded_env)
    assert counts["online"] == 3


def test_empty_db_returns_zero_counts():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    SqliteWriter(db_path).close()
    counts = get_wall_filter_counts(db_path)
    assert counts == {"all": 0, "online": 0, "signal_lost": 0, "no_signal": 0}
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_resolved_events_dont_count(seeded_env):
    """已解決的事件不應讓 cam 計入 abnormal。"""
    import sqlite3
    conn = sqlite3.connect(seeded_env)
    try:
        # 直接 UPDATE resolved_at（不需要 scan_run）
        conn.execute(
            "UPDATE events SET resolved_at = '2026-07-29T03:00:00Z' WHERE device_id = 'c1'"
        )
        conn.commit()
    finally:
        conn.close()

    counts = get_wall_filter_counts(seeded_env)
    # c1 解決後應回到 online
    assert counts["signal_lost"] == 0
    assert counts["online"] == 4