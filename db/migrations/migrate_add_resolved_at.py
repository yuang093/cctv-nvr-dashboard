"""
db/migrations/migrate_add_resolved_at.py
=======================================
離線版 migration 腳本：用於「沒跑過 SqliteWriter 也要加欄位」的場景。

正常使用下不需要跑這個 — SqliteWriter.__init__ 啟動時會自動跑。
只有在「DB 已經存在、但升級前不能開 SqliteWriter」時才需要（罕見）。

使用：
    python db/migrations/migrate_add_resolved_at.py [db_path]

預設 db_path = ./nvr_scan.db（從 cwd 計算）
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
        # 確認 events 表存在
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        ]
        if "events" not in tables:
            print(f"[skip] {db_path} 沒有 events 表（可能還沒初始化）")
            return 0

        # 檢查欄位
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        if "resolved_at" in cols:
            print(f"[skip] {db_path} 已含 resolved_at 欄位（idempotent）")
            return 0

        # 套用
        conn.execute("ALTER TABLE events ADD COLUMN resolved_at TEXT")
        conn.commit()
        print(f"[ok] {db_path} events 表已加 resolved_at 欄位")
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
