"""
tests/test_recording_status_route.py
=====================================
DB helper：取得相機牆用的「24h 錄影完整率」（recording_pct）。

- 沒 recording_status 紀錄 → None（不在 wall 顯示）
- 有紀錄 → 0.0~1.0

純 DB 讀，無 Flask。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_latest_recording_pct


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
    w.upsert_cameras(
        nvra,
        {
            "c1": {"name": "cam1", "connection_state": "CONNECTED"},
            "c2": {"name": "cam2", "connection_state": "CONNECTED"},
        },
    )
    yield w, db_path, nvra
    # 把 fixture 寫入 sync 到 disk（否則 web.db._connect 開新連線讀不到）
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_get_latest_recording_pct_returns_none_when_no_data(db_env):
    """沒 recording_status 紀錄 → 回 None。"""
    _, db_path, nvra = db_env
    assert get_latest_recording_pct(db_path, nvra, "c1") is None


def test_get_latest_recording_pct_returns_value(db_env):
    """有紀錄 → 回 0.0~1.0。"""
    w, db_path, nvra = db_env
    w.upsert_recording_status(
        nvra,
        "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.83,
        missing_seconds=4 * 3600,
    )
    # commit 並關閉 writer，模擬 worker scrape 結束後 web 讀
    w._get_conn().commit()
    w.close()
    val = get_latest_recording_pct(db_path, nvra, "c1")
    assert val == pytest.approx(0.83)


def test_get_latest_recording_pct_only_for_requested_camera(db_env):
    """不同 cam 的完整率應分開回。"""
    w, db_path, nvra = db_env
    w.upsert_recording_status(
        nvra,
        "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.5,
        missing_seconds=12 * 3600,
    )
    w.upsert_recording_status(
        nvra,
        "c2",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.9,
        missing_seconds=2 * 3600,
    )
    w._get_conn().commit()
    w.close()
    assert get_latest_recording_pct(db_path, nvra, "c1") == pytest.approx(0.5)
    assert get_latest_recording_pct(db_path, nvra, "c2") == pytest.approx(0.9)
