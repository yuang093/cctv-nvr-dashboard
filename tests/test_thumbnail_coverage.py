"""
tests/test_thumbnail_coverage.py
==================================
2026-07-31（Task B，Spec B+）：雲端覆蓋（cam 有/無 thumbnail）。

「雲端覆蓋」定義：cam 是否有 image_health_checks 紀錄。
- 有紀錄 = 「有縮圖」= 雲端有資料
- 沒紀錄 = 「無縮圖」= 雲端沒資料

注意：image_health_checks.camera_id 是 TEXT（沒有 FK 連 cameras 表），
需要 LEFT JOIN 用 cameras.device_id 對應。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

from db.sqlite_writer import SqliteWriter
from web.fleet import get_thumbnail_coverage


def _seed_app(db_path: str) -> None:
    """Seed：2 NVR、5 真 cam、1 ghost cam，2 台有縮圖。"""
    w = SqliteWriter(db_path)
    nvr_a = w.upsert_nvr({
        "id": "NVR-A", "name": "A 分店", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    nvr_b = w.upsert_nvr({
        "id": "NVR-B", "name": "B 分店", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    })
    # NVR-A 3 cam
    rid = w.begin_scan_run("2026-07-31T06:00:00Z")
    w.upsert_cameras(nvr_a, {
        "d1": {"name": "大門"},
        "d2": {"name": "後門"},
        "d3": {"name": "倉庫"},
    })
    w.finish_scan_run(
        rid, finished_at="2026-07-31T06:00:01Z", status="success",
        stats={"total_cameras": 3, "abnormal_cameras": 0,
               "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0},
    )
    # NVR-B 2 cam + ghost d99
    rid2 = w.begin_scan_run("2026-07-31T06:01:00Z")
    w.upsert_cameras(nvr_b, {
        "d4": {"name": "B 大門"},
        "d5": {"name": "B 後門"},
        "d99": {"name": "ghost B"},
    })
    w.finish_scan_run(
        rid2, finished_at="2026-07-31T06:01:01Z", status="success",
        stats={"total_cameras": 3, "abnormal_cameras": 0,
               "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0},
    )
    del w  # 關閉，讓 sqlite3 直接寫入

    # mark d99 為 ghost
    con = sqlite3.connect(db_path)
    con.execute("UPDATE cameras SET is_ghost = 1 WHERE device_id = 'd99'")
    con.commit()
    con.close()

    # 寫 image_health_checks：d1, d3 有縮圖
    now = datetime.now(timezone.utc).isoformat()
    for cam_id, nvr_int_id in [("d1", nvr_a), ("d3", nvr_a)]:
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO image_health_checks "
            "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (cam_id, nvr_int_id, now, "{}", "[]"),
        )
        con.commit()
        con.close()


@pytest.fixture
def app_with_thumbnails():
    """2 NVR、5 真 cam、1 ghost cam，2 台有縮圖（d1, d3）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    _seed_app(db_path)
    yield db_path
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 1. 基本：跨 NVR 合計 有/無縮圖 ===
def test_thumbnail_coverage_counts(app_with_thumbnails):
    """d1+d3 有縮圖（共 2），d2/d4/d5 無縮圖（共 3），d99 ghost 不計入。"""
    db_path = app_with_thumbnails
    result = get_thumbnail_coverage(db_path)
    assert result["with_thumbnail"] == 2
    assert result["without_thumbnail"] == 3
    assert result["total"] == 5
    assert result["ghost_count"] == 1  # d99


# === 2. ghost cam 不應計入 ===
def test_thumbnail_coverage_excludes_ghost(app_with_thumbnails):
    """ghost cam 有縮圖也不應計入。"""
    db_path = app_with_thumbnails
    # 給 ghost cam 也寫一筆 image_health_checks → 仍不應被算進 with_thumbnail
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO image_health_checks "
        "(camera_id, checked_at_utc, metrics_json, flags_json) "
        "VALUES (?, ?, ?, ?)",
        ("d99", "2026-07-31T06:30:00Z", "{}", "[]"),
    )
    con.commit()
    con.close()

    result = get_thumbnail_coverage(db_path)
    assert result["with_thumbnail"] == 2  # 沒變（不算 ghost）
    assert result["without_thumbnail"] == 3
    assert result["total"] == 5  # 沒變（不算 ghost）
    assert result["ghost_count"] == 1


# === 3. 空 DB：沒有任何 cam 也不應炸 ===
def test_thumbnail_coverage_empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    # 只要建表（SqliteWriter() ctor 會建）
    SqliteWriter(db_path)

    try:
        result = get_thumbnail_coverage(db_path)
        assert result["with_thumbnail"] == 0
        assert result["without_thumbnail"] == 0
        assert result["total"] == 0
        assert result["ghost_count"] == 0
    finally:
        Path(db_path).unlink()
