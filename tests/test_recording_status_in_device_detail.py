"""
tests/test_recording_status_in_device_detail.py
================================================
驗證 `get_recording_status_for_camera(db_path, nvr_id, camera_id)`。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_recording_status_for_camera


@pytest.fixture
def db_env():
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
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {"c1": {"name": "cam1", "connection_state": "CONNECTED"}})
    yield w, db_path, nvra
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_get_recording_status_returns_none_when_no_data(db_env):
    _, db_path, nvra = db_env
    assert get_recording_status_for_camera(db_path, nvra, "c1") is None


def test_get_recording_status_returns_full_dict(db_env):
    w, db_path, nvra = db_env
    w.upsert_recording_status(
        nvra,
        "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.7,
        missing_seconds=7 * 3600,
    )
    w._get_conn().commit()
    w.close()

    info = get_recording_status_for_camera(db_path, nvra, "c1")
    assert info is not None
    assert info["completeness"] == pytest.approx(0.7)
    assert info["missing_seconds"] == pytest.approx(7 * 3600)
    assert info["window_start"] == "2026-07-28T00:00:00Z"
    assert info["window_end"] == "2026-07-29T00:00:00Z"
    assert info["checked_at"]  # 自動填
    assert info["missing_hours"] == pytest.approx(7.0)
