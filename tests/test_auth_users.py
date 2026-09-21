"""Week 5 #012：users 表 + 預設 admin + IP 白名單測試（Task 6）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def user_db(tmp_path: Path) -> str:
    """建立含 users 表 + 預設 admin 的 SQLite。"""
    from db.migrations.migrate_create_users import run as migrate_users

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
    migrate_users(db_path, admin_default_password="testpass123")
    return db_path


def test_users_table_created_with_admin(user_db: str) -> None:
    """migration 建立 users 表 + 預設 admin 帳號。"""
    from web.auth.users import get_user_by_username

    admin = get_user_by_username(user_db, "admin")
    assert admin is not None
    assert admin.username == "admin"
    assert admin.must_change_password is True


def test_verify_admin_password(user_db: str) -> None:
    """verify_password 正確性。"""
    from web.auth.users import get_user_by_username

    admin = get_user_by_username(user_db, "admin")
    assert admin.verify_password("testpass123") is True
    assert admin.verify_password("wrong") is False


def test_change_password_clears_must_change_flag(user_db: str) -> None:
    """change_password 後 must_change_password → False。"""
    from web.auth.users import get_user_by_username, change_password

    admin = get_user_by_username(user_db, "admin")
    change_password(user_db, admin.id, "newpass456")

    admin2 = get_user_by_username(user_db, "admin")
    assert admin2.must_change_password is False
    assert admin2.verify_password("newpass456") is True
    assert admin2.verify_password("testpass123") is False


def test_get_user_by_id(user_db: str) -> None:
    """get_user_by_id 正確查找。"""
    from web.auth.users import get_user_by_id, get_user_by_username

    admin = get_user_by_username(user_db, "admin")
    found = get_user_by_id(user_db, admin.id)
    assert found is not None
    assert found.username == "admin"
    # 不存在 id
    assert get_user_by_id(user_db, 99999) is None


def test_is_trusted_ip_with_default_cidrs() -> None:
    """驗證 is_trusted_ip 全套 CIDR。"""
    from web.auth.ip_whitelist import is_trusted_ip

    cidrs = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    # 內網
    assert is_trusted_ip("127.0.0.1", cidrs) is True
    assert is_trusted_ip("192.168.1.10", cidrs) is True
    assert is_trusted_ip("10.5.5.5", cidrs) is True
    assert is_trusted_ip("172.20.0.1", cidrs) is True
    # 邊界
    assert is_trusted_ip("172.31.255.255", cidrs) is True
    assert is_trusted_ip("172.32.0.0", cidrs) is False
    # 外網
    assert is_trusted_ip("8.8.8.8", cidrs) is False
    assert is_trusted_ip("203.0.113.5", cidrs) is False


def test_is_trusted_ip_invalid_ip() -> None:
    """無效 IP 應回傳 False（不崩潰）。"""
    from web.auth.ip_whitelist import is_trusted_ip

    assert is_trusted_ip("not-an-ip", ("192.168.0.0/16",)) is False
    assert is_trusted_ip("", ("192.168.0.0/16",)) is False


def test_is_trusted_ip_invalid_cidr_skipped() -> None:
    """CIDR 列表中有無效項應跳過（不崩潰）。"""
    from web.auth.ip_whitelist import is_trusted_ip

    # 即使有一個 garbage CIDR，仍能正確判定
    cidrs = ("not-a-cidr", "192.168.0.0/16", "another-bad")
    assert is_trusted_ip("192.168.1.1", cidrs) is True
    assert is_trusted_ip("8.8.8.8", cidrs) is False


def test_migration_idempotent(tmp_path: Path) -> None:
    """重跑 migration 不應壞（admin 已存在 → [skip]）。"""
    from db.migrations.migrate_create_users import run as migrate_users

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

    assert migrate_users(db_path, admin_default_password="pass1") == 0
    # 第二次跑：admin 已存在，不重建
    assert migrate_users(db_path, admin_default_password="pass2") == 0

    # admin 密碼仍是 pass1（不會被覆寫）
    from web.auth.users import get_user_by_username

    admin = get_user_by_username(db_path, "admin")
    assert admin.verify_password("pass1") is True
    assert admin.verify_password("pass2") is False


def test_record_login_updates_timestamp(user_db: str) -> None:
    """record_login 寫入 last_login_at。"""
    from web.auth.users import get_user_by_username, record_login

    admin = get_user_by_username(user_db, "admin")
    assert admin is not None
    record_login(user_db, admin.id)
    # 重抓應看到 last_login_at
    conn = sqlite3.connect(user_db)
    try:
        row = conn.execute(
            "SELECT last_login_at FROM users WHERE id = ?", (admin.id,)
        ).fetchone()
        assert row[0] is not None
    finally:
        conn.close()
