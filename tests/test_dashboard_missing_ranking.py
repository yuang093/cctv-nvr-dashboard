"""
tests/test_dashboard_missing_ranking.py
========================================
Dashboard 統計：「24h 缺錄最多」排名（取 recording_status.missing_seconds 前 N 名）。

輸出：
- list of dict: [{"camera_id", "camera_name", "nvr_name", "missing_hours", "missing_seconds", "completeness"}, ...]
- 缺錄越多越靠前
- 沒資料 → 空 list
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_top_missing_cameras


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
            "c3": {"name": "cam3", "connection_state": "CONNECTED"},
        },
    )
    w.upsert_recording_status(
        nvra,
        "c1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.9,
        missing_seconds=2 * 3600,
    )
    w.upsert_recording_status(
        nvra,
        "c2",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.1,
        missing_seconds=21.6 * 3600,
    )
    w.upsert_recording_status(
        nvra,
        "c3",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.5,
        missing_seconds=12.0 * 3600,
    )
    w._get_conn().commit()
    w.close()
    yield db_path
    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def test_get_top_missing_cameras_returns_empty_when_no_data():
    """完全沒 recording_status 紀錄 → 空 list。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        # 啟動 SqliteWriter 觸發 schema init（建立 recording_status 表）
        w = SqliteWriter(db_path)
        w.close()
        rows = get_top_missing_cameras(db_path, limit=5)
        assert rows == []
    finally:
        try:
            Path(db_path).unlink()
        except OSError:
            pass


def test_get_top_missing_cameras_sorted_desc(db_env):
    """結果按 missing_seconds 降序排序。"""
    rows = get_top_missing_cameras(db_env, limit=5)
    assert len(rows) == 3
    assert rows[0]["camera_id"] == "c2"  # 21.6h
    assert rows[1]["camera_id"] == "c3"  # 12.0h
    assert rows[2]["camera_id"] == "c1"  # 2.0h


def test_get_top_missing_cameras_includes_nvr_name_and_camera_name(db_env):
    """每筆應含 nvr_name + camera_name（給 UI 顯示）。"""
    rows = get_top_missing_cameras(db_env, limit=5)
    for r in rows:
        assert r["nvr_name"] == "A"
        assert r["camera_name"].startswith("cam")


def test_get_top_missing_cameras_limit(db_env):
    """limit 參數：只取前 N 名。"""
    rows = get_top_missing_cameras(db_env, limit=2)
    assert len(rows) == 2
    assert rows[0]["camera_id"] == "c2"
    assert rows[1]["camera_id"] == "c3"


def test_get_top_missing_cameras_calculates_missing_hours(db_env):
    """missing_hours 應為 missing_seconds / 3600。"""
    rows = get_top_missing_cameras(db_env, limit=5)
    assert rows[0]["missing_hours"] == pytest.approx(21.6)
    assert rows[0]["missing_seconds"] == pytest.approx(21.6 * 3600)
    assert rows[0]["completeness"] == pytest.approx(0.1)


def test_get_top_missing_cameras_excludes_disabled_nvrs(db_env):
    """disabled NVR 的 recording_status 不應出現在 top missing（dashboard 不顯示已停用的舊資料）。

    場景：dedup 後保留了 nvr_servers 兩列同 host，舊的那列停用後不該被算入。
    """
    import sqlite3

    conn = sqlite3.connect(db_env)
    try:
        # 停用 NVR-A
        nvr_a_id = conn.execute("SELECT id FROM nvr_servers WHERE name='A'").fetchone()[
            0
        ]
        conn.execute("UPDATE nvr_servers SET enabled=0 WHERE id=?", (nvr_a_id,))
        conn.commit()
    finally:
        conn.close()

    rows = get_top_missing_cameras(db_env, limit=5)
    assert rows == [], "停用 NVR 的舊 recording_status 不該列入排名"


def test_get_top_missing_cameras_only_shows_enabled_nvr_when_mixed(db_env):
    """混合場景：兩台 NVR，一啟用一停用，只有啟用的 NVR 資料出現。"""
    import sqlite3

    w = SqliteWriter(db_env)
    nvrb = w.upsert_nvr(
        {
            "id": "NVR-B",
            "name": "B",
            "host": "10.0.0.2",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_recording_status(
        nvrb,
        "x1",
        window_start="2026-07-28T00:00:00Z",
        window_end="2026-07-29T00:00:00Z",
        completeness=0.2,
        missing_seconds=19.2 * 3600,
    )
    w._get_conn().commit()
    w.close()

    # A 啟用（fixture 預設 enabled=1）
    # B 預設也是 enabled=1 → 兩台都該出現
    rows = get_top_missing_cameras(db_env, limit=5)
    nvr_names = {r["nvr_name"] for r in rows}
    assert "A" in nvr_names
    assert "B" in nvr_names

    # 把 B 停用 → 只剩 A
    conn = sqlite3.connect(db_env)
    try:
        nvrb_id = conn.execute("SELECT id FROM nvr_servers WHERE name='B'").fetchone()[
            0
        ]
        conn.execute("UPDATE nvr_servers SET enabled=0 WHERE id=?", (nvrb_id,))
        conn.commit()
    finally:
        conn.close()

    rows = get_top_missing_cameras(db_env, limit=5)
    nvr_names = {r["nvr_name"] for r in rows}
    assert "A" in nvr_names
    assert "B" not in nvr_names, "停用的 B 不該出現"
