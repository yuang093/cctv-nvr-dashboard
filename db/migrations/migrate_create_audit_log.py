"""Week 5 #015：建立 audit_log 表。

欄位：
- id (自增 PK)
- user_id (FK→users.id，可 NULL：ops_auto 不綁 user)
- event (login / login_failed / logout / access_denied / query / toggle_nvr)
- ip (來源 IP)
- user_agent (瀏覽器 UA)
- payload_json (JSON 字串，可選)
- created_at (ISO 8601 UTC+8)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NULL,
    event TEXT NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NULL,
    payload_json TEXT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_event ON audit_log(event);
"""


def run(db_path: str) -> int:
    """建立 audit_log 表（idempotent）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        # 確保 schema_migrations 表存在
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        # 檢查表是否已存在
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if existing:
            print("[skip] audit_log 已存在")
            return 0
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (6, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        print("[ok] audit_log 已建立")
        return 0
    finally:
        conn.close()
