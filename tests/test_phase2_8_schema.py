"""
tests/test_phase2_8_schema.py
==============================
Phase 2.8（Arisan DB schema）：驗證 3 新表 + 1 改欄位 + 17 seed + idempotent。

覆蓋：
  - image_health_checks 表存在 + 索引存在
  - discover_sessions 表存在
  - event_kind_catalog 表存在 + 17 筆 seed 全到 + sort_order 正確
  - cameras.last_health_check_id 欄位存在
  - Idempotent：重跑 _init_schema 不爆（CREATE IF NOT EXISTS + ALTER 跳過已存在）
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter


@pytest.fixture
def fresh_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 1. 3 表都存在 ===
def test_image_health_checks_table_exists(fresh_db_path):
    """image_health_checks 表 + 索引 應被自動建立。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "image_health_checks" in tables
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_health_cam_time" in indexes
        assert "idx_health_nvr" in indexes
    finally:
        conn.close()


def test_discover_sessions_table_exists(fresh_db_path):
    """discover_sessions 表應被自動建立。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "discover_sessions" in tables
    finally:
        conn.close()


def test_event_kind_catalog_table_exists(fresh_db_path):
    """event_kind_catalog 表應被自動建立。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "event_kind_catalog" in tables
    finally:
        conn.close()


# === 2. 17 筆 seed ===
def test_event_kind_catalog_has_17_seeds(fresh_db_path):
    """Phase 2.8 seed：17 筆（8 DEVICE_* + 9 STATE_*）+ STATE_CONNECTING is_fault=0。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM event_kind_catalog").fetchone()[0]
        assert count == 17, f"預期 17 筆，實際 {count}"

        # 類別分布
        device_count = conn.execute(
            "SELECT COUNT(*) FROM event_kind_catalog WHERE category='DEVICE'"
        ).fetchone()[0]
        state_count = conn.execute(
            "SELECT COUNT(*) FROM event_kind_catalog WHERE category='STATE'"
        ).fetchone()[0]
        assert device_count == 8
        assert state_count == 9

        # STATE_CONNECTING 是資訊性事件（is_fault=0），其餘 16 筆 is_fault=1
        is_fault_0 = conn.execute(
            "SELECT COUNT(*) FROM event_kind_catalog WHERE is_fault=0"
        ).fetchone()[0]
        is_fault_1 = conn.execute(
            "SELECT COUNT(*) FROM event_kind_catalog WHERE is_fault=1"
        ).fetchone()[0]
        assert is_fault_0 == 1
        assert is_fault_1 == 16

        # STATE_CONNECTING 確實是 is_fault=0 那筆
        topic = conn.execute(
            "SELECT event_topic FROM event_kind_catalog WHERE is_fault=0"
        ).fetchone()[0]
        assert topic == "STATE_CONNECTING"
    finally:
        conn.close()


def test_event_kind_catalog_chinese_names_present(fresh_db_path):
    """name_zh 不可為空，且至少包含預期關鍵字。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        rows = conn.execute(
            "SELECT event_topic, name_zh FROM event_kind_catalog"
        ).fetchall()
        topics = {r[0] for r in rows}
        # 抽查幾個關鍵翻譯
        assert "影像訊號斷線" in next(
            r[1] for r in rows if r[0] == "DEVICE_VIDEO_SIGNAL_LOST"
        )
        assert "破壞" in next(r[1] for r in rows if r[0] == "DEVICE_TAMPERING")
        assert "場景改變" in next(r[1] for r in rows if r[0] == "DEVICE_TAMPERING")
        assert "連線中" in next(r[1] for r in rows if r[0] == "STATE_CONNECTING")
    finally:
        conn.close()


def test_event_kind_catalog_sort_order_unique(fresh_db_path):
    """sort_order 應互不重複（UI 顯示順序穩定）。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        orders = [
            r[0]
            for r in conn.execute(
                "SELECT sort_order FROM event_kind_catalog"
            ).fetchall()
        ]
        assert len(orders) == len(set(orders)), f"sort_order 有重複：{orders}"
    finally:
        conn.close()


# === 3. cameras.last_health_check_id ===
def test_cameras_last_health_check_id_column(fresh_db_path):
    """cameras 表應有 last_health_check_id 欄位（nullable）。"""
    SqliteWriter(fresh_db_path)
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cameras)").fetchall()}
        assert "last_health_check_id" in cols
    finally:
        conn.close()


# === 4. Idempotent：重跑 _init_schema 不爆 ===
def test_init_schema_idempotent(fresh_db_path):
    """重複建立 SqliteWriter（會重跑 _init_schema）不應 crash。"""
    SqliteWriter(fresh_db_path)  # 第 1 次
    SqliteWriter(fresh_db_path)  # 第 2 次
    SqliteWriter(fresh_db_path)  # 第 3 次

    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        # 17 seed 不應被 INSERT OR IGNORE 重複新增（仍是 17）
        count = conn.execute("SELECT COUNT(*) FROM event_kind_catalog").fetchone()[0]
        assert count == 17
        # last_health_check_id 仍只有一個（不應被 ALTER 加上第二個）
        col_count = conn.execute(
            "SELECT COUNT(*) FROM pragma_table_info('cameras') "
            "WHERE name='last_health_check_id'"
        ).fetchone()[0]
        assert col_count == 1
    finally:
        conn.close()


def test_init_schema_does_not_drop_existing_data(fresh_db_path):
    """重跑 _init_schema 不應清空既有 NVR / cameras / events 資料。"""
    w = SqliteWriter(fresh_db_path)
    nvr_int = w.upsert_nvr(
        {
            "id": "n1",
            "name": "N1",
            "host": "1.1.1.1",
            "username": "u",
            "password": "p",
        }
    )
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.upsert_cameras(nvr_int, {"d1": "cam1"})
    w.insert_events(
        rid,
        nvr_int,
        [
            {
                "eventId": "e1",
                "deviceId": "d1",
                "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
                "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
                "occurred_at": "2026-06-23T00:00:00Z",
            }
        ],
    )
    w.finish_scan_run(
        rid,
        finished_at="2026-06-23T00:01:00Z",
        status="success",
        stats={
            "total_cameras": 1,
            "abnormal_cameras": 1,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )

    # 重跑 init
    SqliteWriter(fresh_db_path)

    # 既有資料仍存在
    import sqlite3

    conn = sqlite3.connect(fresh_db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        # 既有 cam 的 last_health_check_id 應為 NULL（沒跑過 image_health）
        last_hc = conn.execute(
            "SELECT last_health_check_id FROM cameras WHERE device_id='d1'"
        ).fetchone()[0]
        assert last_hc is None
    finally:
        conn.close()
