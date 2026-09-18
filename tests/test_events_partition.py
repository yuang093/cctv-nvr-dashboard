"""Week 3 #008/#009：events 表分區 Phase 1 測試。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.migrations.migrate_add_events_partition import run as run_partition_migration


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """建立含 events 表的乾淨 DB（模擬既有 schema）。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL
        );
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL,
            device_id TEXT NOT NULL,
            UNIQUE(nvr_id, device_id)
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_run_id INTEGER NOT NULL,
            nvr_id INTEGER NOT NULL,
            camera_id INTEGER NULL,
            event_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            event_topic TEXT NOT NULL,
            event_topics_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            resolved_at TEXT NULL,
            raw_json TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def test_migration_is_idempotent(fresh_db: str, capsys) -> None:
    """跑 2 次 migration 都不應該壞。"""
    assert run_partition_migration(fresh_db) == 0
    out1 = capsys.readouterr().out

    assert run_partition_migration(fresh_db) == 0
    out2 = capsys.readouterr().out

    # 第二次應該印 [skip]
    assert "[skip]" in out2 or "已" in out2
    # 第一次 [ok] 或 [created]
    assert "[ok]" in out1 or "[created]" in out1


def test_select_through_view_returns_legacy_data(fresh_db: str) -> None:
    """SELECT FROM events 走 view，應能讀到 legacy 資料。"""
    # 先塞 legacy 資料
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO events (scan_run_id, nvr_id, event_id, device_id, "
        "event_topic, event_topics_json, occurred_at, detected_at, raw_json) "
        "VALUES (1, 1, 'evt-1', 'd1', 'VIDEO_LOSS', '[\"VIDEO_LOSS\"]', "
        "'2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z', '{}')"
    )
    conn.commit()
    conn.close()

    # 跑 migration
    run_partition_migration(fresh_db)

    # 驗證 SELECT 走 view 能讀到
    conn2 = sqlite3.connect(fresh_db)
    rows = conn2.execute(
        "SELECT event_id, event_topic FROM events WHERE device_id='d1'"
    ).fetchall()
    conn2.close()

    assert len(rows) == 1
    assert rows[0][0] == "evt-1"
    assert rows[0][1] == "VIDEO_LOSS"


def test_insert_into_view_routes_to_monthly_table(fresh_db: str) -> None:
    """INSERT INTO events 透過 INSTEAD OF trigger 寫入當月表。"""
    run_partition_migration(fresh_db)

    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO events (scan_run_id, nvr_id, event_id, device_id, "
        "event_topic, event_topics_json, occurred_at, detected_at, raw_json) "
        "VALUES (1, 1, 'evt-new', 'd2', 'TAMPERING', '[\"TAMPERING\"]', "
        "'2026-09-18T10:00:00Z', '2026-09-18T10:00:00Z', '{}')"
    )
    conn.commit()

    # 從當月表直接查，應有 1 筆
    from db.migrations.migrate_add_events_partition import (
        _current_month_table_name,
    )

    table = _current_month_table_name()
    rows = conn.execute(
        f"SELECT event_id FROM {table} WHERE device_id='d2'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "evt-new"
