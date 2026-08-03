"""
tests/test_recording_status.py
==============================
錄影完整率（recording_status 表）DB 層測試。

目的：確保 scanner 寫入的 24h 完整率 / 缺段小時數能正確存 DB。
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter


@pytest.fixture
def writer():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    yield w, db_path
    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_recording_status_table_exists(writer):
    """SqliteWriter 啟動後 creating recording_status 表。"""
    w, db_path = writer
    conn = w._get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recording_status'"
    ).fetchall()
    assert rows, "recording_status 表未被建立"


def test_recording_status_unique_per_camera(writer):
    """recording_status 表應有 (nvr_id, camera_id) UNIQUE 約束。"""
    w, db_path = writer
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {"c1": {"name": "cam1", "connection_state": "CONNECTED"}})

    w.upsert_recording_status(
        nvra, "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.5,
        missing_seconds=12 * 3600,
    )
    # 第二次 upsert 同一 (nvr, camera) 應覆蓋
    w.upsert_recording_status(
        nvra, "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.9,
        missing_seconds=2 * 3600,
    )

    conn = w._get_conn()
    rows = conn.execute(
        "SELECT camera_id, completeness, missing_seconds FROM recording_status"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "c1"
    assert rows[0][1] == pytest.approx(0.9)
    assert rows[0][2] == 2 * 3600


def test_recording_status_round_trip(writer):
    """寫入後能讀回完整欄位。"""
    w, db_path = writer
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {"c1": {"name": "cam1", "connection_state": "CONNECTED"}})

    w.upsert_recording_status(
        nvra, "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.228,
        missing_seconds=17.5 * 3600,
    )

    conn = w._get_conn()
    row = conn.execute(
        "SELECT camera_id, window_start, window_end, completeness, missing_seconds, checked_at"
        " FROM recording_status WHERE camera_id=?",
        ("c1",),
    ).fetchone()
    assert row[0] == "c1"
    assert row[1] == "2026-07-28T00:00:00Z"
    assert row[2] == "2026-07-29T00:00:00Z"
    assert row[3] == pytest.approx(0.228)
    assert row[4] == pytest.approx(17.5 * 3600)
    assert row[5]  # checked_at 自動填
