"""Week 5 #012：User 模型 + 密碼管理。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash


class User(UserMixin):
    """Flask-Login 相容的 User 物件。"""

    def __init__(
        self,
        id: int,
        username: str,
        password_hash: str,
        must_change_password: bool,
    ) -> None:
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.must_change_password = must_change_password

    @staticmethod
    def from_row(row: sqlite3.Row) -> "User":
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            must_change_password=bool(row["must_change_password"]),
        )

    def verify_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


def get_user_by_id(db_path: str, user_id: int) -> Optional[User]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, must_change_password FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return User.from_row(row) if row else None
    finally:
        conn.close()


def get_user_by_username(db_path: str, username: str) -> Optional[User]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, must_change_password FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return User.from_row(row) if row else None
    finally:
        conn.close()


def change_password(db_path: str, user_id: int, new_password: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = 0
            WHERE id = ?
            """,
            (generate_password_hash(new_password), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_login(db_path: str, user_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (datetime.now(timezone(timedelta(hours=8))).isoformat(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
