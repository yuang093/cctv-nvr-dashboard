"""
db/migrations/migrate_add_nvr_enabled.py
=========================================
離線版 migration 腳本：用於「沒跑過 SqliteWriter 也要加欄位」的場景。

使用：
    python db/migrations/migrate_add_nvr_enabled.py [db_path]

退出碼：0 = 已套用（或已存在）/ 1 = 失敗
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def run(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 確認 nvr_servers 表存在
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        ]
        if "nvr_servers" not in tables:
            print(f"[skip] {db_path} 沒有 nvr_servers 表（可能還沒初始化）")
            return 0

        # 檢查欄位
        cols = [
            r["name"] for r in conn.execute("PRAGMA table_info(nvr_servers)").fetchall()
        ]
        if "enabled" in cols:
            print(f"[skip] {db_path} 已含 enabled 欄位（idempotent）")
            return 0

        # 套用
        conn.execute(
            "ALTER TABLE nvr_servers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
        conn.commit()
        print(f"[ok] {db_path} nvr_servers 已加 enabled 欄位（預設全啟用）")
        return 0
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "./nvr_scan.db"
    if db != ":memory:":
        Path(db).parent.mkdir(parents=True, exist_ok=True)
    sys.exit(run(db))
