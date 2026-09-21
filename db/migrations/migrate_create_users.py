"""Week 5 #012：建立 users 表 + 預設 admin 帳號。

欄位：
- id (PK)
- username (unique, case-sensitive)
- password_hash (werkzeug pbkdf2)
- must_change_password (BOOL，首次登入強制改密碼)
- last_login_at
- created_at
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT NULL,
    created_at TEXT NOT NULL
);
"""


def run(db_path: str, admin_default_password: str = "admin") -> int:
    """建立 users 表（idempotent）+ 補建 admin 帳號（若不存在）。

    Args:
        db_path: SQLite 檔路徑
        admin_default_password: admin 預設密碼（首次啟動時用）
    """
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

        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not existing:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (7, datetime.now(timezone.utc).isoformat()),
            )

        # 建立 / 補建 admin 帳號
        admin_row = conn.execute(
            "SELECT id FROM users WHERE username = 'admin'"
        ).fetchone()
        if not admin_row:
            from werkzeug.security import generate_password_hash

            conn.execute(
                """
                INSERT INTO users (username, password_hash, must_change_password, created_at)
                VALUES (?, ?, 1, ?)
                """,
                (
                    "admin",
                    generate_password_hash(admin_default_password),
                    datetime.now(timezone(timedelta(hours=8))).isoformat(),
                ),
            )
            conn.commit()
            print(
                f"[ok] admin 帳號已建立（密碼={admin_default_password}；首次登入強制改密碼）"
            )
        else:
            print("[skip] admin 帳號已存在")
        return 0
    finally:
        conn.close()
