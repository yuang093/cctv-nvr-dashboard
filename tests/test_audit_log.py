"""Week 5 #015：audit log 表 + write helper + rotate 測試（Task 2）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def audit_db(tmp_path: Path) -> str:
    """建立含 audit_log 表的 SQLite。"""
    from db.migrations.migrate_create_audit_log import run as migrate_audit

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """
    )
    conn.commit()
    conn.close()
    migrate_audit(db_path)
    return db_path


def test_audit_log_table_created(audit_db: str) -> None:
    """migration 建立 audit_log 表 + 3 個 indexes。"""
    conn = sqlite3.connect(audit_db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
        assert "id" in cols
        assert "user_id" in cols
        assert "event" in cols
        assert "ip" in cols
        assert "user_agent" in cols
        assert "payload_json" in cols
        assert "created_at" in cols
        # 3 個 indexes
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='audit_log'"
        ).fetchall()
        names = {r[0] for r in idx}
        assert "idx_audit_log_created_at" in names
        assert "idx_audit_log_user_id" in names
        assert "idx_audit_log_event" in names
    finally:
        conn.close()


def test_write_audit_event_persists(audit_db: str) -> None:
    """write_audit_event 寫入一筆記錄，欄位正確。"""
    from audit.log import write_audit_event

    write_audit_event(
        db_path=audit_db,
        event="login",
        ip="192.168.1.10",
        user_agent="Mozilla/5.0",
        user_id=1,
        payload={"method": "password"},
    )
    conn = sqlite3.connect(audit_db)
    try:
        rows = conn.execute(
            "SELECT event, ip, user_agent, user_id, payload_json FROM audit_log"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "login"
        assert rows[0][1] == "192.168.1.10"
        assert rows[0][2] == "Mozilla/5.0"
        assert rows[0][3] == 1
        assert "password" in rows[0][4]
    finally:
        conn.close()


def test_audit_log_idempotent_migration(audit_db: str) -> None:
    """重跑 migration 006 不會壞（[skip] 或 [ok] 皆可）。"""
    from db.migrations.migrate_create_audit_log import run as migrate_audit

    assert migrate_audit(audit_db) == 0
    assert migrate_audit(audit_db) == 0


def test_audit_log_rotation_deletes_old(audit_db: str) -> None:
    """rotate_audit_log 刪除 > retention_days 的紀錄。"""
    from audit.log import write_audit_event, rotate_audit_log

    old_date = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    new_date = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(audit_db)
    conn.execute(
        "INSERT INTO audit_log (event, ip, created_at) VALUES (?, ?, ?)",
        ("login", "10.0.0.1", old_date),
    )
    conn.execute(
        "INSERT INTO audit_log (event, ip, created_at) VALUES (?, ?, ?)",
        ("login", "10.0.0.2", new_date),
    )
    conn.commit()
    conn.close()

    deleted = rotate_audit_log(audit_db, retention_days=90)
    assert deleted == 1

    conn = sqlite3.connect(audit_db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_audit_log_handles_none_payload(audit_db: str) -> None:
    """payload=None → payload_json 為 NULL（不應序列化 'null' 字串）。"""
    from audit.log import write_audit_event

    write_audit_event(db_path=audit_db, event="logout", ip="127.0.0.1")
    conn = sqlite3.connect(audit_db)
    try:
        row = conn.execute(
            "SELECT payload_json, user_id FROM audit_log"
        ).fetchone()
        assert row[0] is None
        assert row[1] is None
    finally:
        conn.close()


def test_audit_log_unicode_payload(audit_db: str) -> None:
    """payload 含中文應正確保存（ensure_ascii=False）。"""
    from audit.log import write_audit_event

    write_audit_event(
        db_path=audit_db,
        event="query",
        ip="192.168.1.1",
        payload={"keyword": "攝影機斷線", "nvr_name": "中正機房"},
    )
    conn = sqlite3.connect(audit_db)
    try:
        row = conn.execute("SELECT payload_json FROM audit_log").fetchone()
        assert "攝影機斷線" in row[0]
        assert "中正機房" in row[0]
    finally:
        conn.close()
