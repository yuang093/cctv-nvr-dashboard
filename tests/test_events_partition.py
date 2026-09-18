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
