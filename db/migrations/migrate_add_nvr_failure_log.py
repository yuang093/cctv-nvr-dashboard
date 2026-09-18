"""
db/migrations/migrate_add_nvr_failure_log.py
============================================
離線版 migration 腳本：用於「沒跑過 SqliteWriter 也要建表」的場景。

正常使用下不需要跑這個 — SqliteWriter.__init__ 啟動時會自動跑。
只有在「DB 已經存在、但升級前不能開 SqliteWriter」時才需要（罕見）。

使用：
    python db/migrations/migrate_add_nvr_failure_log.py [db_path]

預設 db_path = ./nvr_scan.db（從 cwd 計算）
退出碼：0 = 已套用（或已存在）/ 1 = 失敗
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nvr_failure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
    nvr_id TEXT NOT NULL,
    nvr_name TEXT NOT NULL,
    nvr_internal_id INTEGER,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    failed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nvr_failure_log_scan_run_id
    ON nvr_failure_log(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_nvr_failure_log_nvr_id_failed_at
    ON nvr_failure_log(nvr_id, failed_at DESC);
"""


def run(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 確認 scan_runs 表存在（避免 FK 找不到目標）
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        ]
        if "scan_runs" not in tables:
            print(f"[skip] {db_path} 沒有 scan_runs 表（可能還沒初始化）")
            return 0

        # 檢查表是否已存在
        if "nvr_failure_log" in tables:
            print(f"[skip] {db_path} 已含 nvr_failure_log 表（idempotent）")
            return 0

        # 套用
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        print(f"[ok] {db_path} nvr_failure_log 表已建立")
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
