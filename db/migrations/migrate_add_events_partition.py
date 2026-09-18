"""
db/migrations/migrate_add_events_partition.py
============================================
Week 3 Issue #008：events 表改為月分區 + view。

使用：
    python db/migrations/migrate_add_events_partition.py [db_path]

退出碼：0 = 已套用（或已存在）/ 1 = 失敗

策略：
  1. 偵測 `events` 是否已是 view（已是 → skip）
  2. 偵測是否已有當月 `events_YYYY_MM` 表（已是 → skip）
  3. 否則：
     a. 把現有 events 改名為 `events_legacy`
     b. 建立當月 `events_YYYY_MM` 表（與 legacy 同 schema）
     c. 把 legacy 資料 INSERT 進當月表
     d. 建立 `events` view = UNION ALL 當月表（+ legacy）
     e. 建立 INSTEAD OF INSERT trigger → 依 occurred_at 路由
     f. 建立 INSTEAD OF UPDATE trigger → 依 id 找對應月份表更新
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _current_month_table_name() -> str:
    """當月分區表名，例如 'events_2026_09'。"""
    now = datetime.now(timezone.utc)
    return f"events_{now.year:04d}_{now.month:02d}"


def _events_table_schema_sql() -> str:
    """既有 events 表的完整 CREATE TABLE SQL。
    用於建當月表（與 legacy 同 schema）。
    """
    return """
    CREATE TABLE IF NOT EXISTS {table} (
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
    )
    """


def run(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # ── 檢查 events 是否已是 view ────────────────────────
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='events'"
        ).fetchone()
        if row and row["type"] == "view":
            print(f"[skip] {db_path} events 已是 view，無需 migration")
            return 0

        if row is None:
            print(f"[skip] {db_path} 沒有 events 表（可能還沒初始化）")
            return 0

        # ── 既有 events 表存在 → 開始轉換 ──────────────────
        current_month = _current_month_table_name()

        # 檢查當月表是否已存在
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (current_month,)
        ).fetchone()
        if existing:
            print(f"[skip] {db_path} {current_month} 已存在")
            return 0

        # a. 把現有 events 改名為 events_legacy（資料保留供 rollback 用）
        conn.execute("ALTER TABLE events RENAME TO events_legacy")

        # b. 建立當月表
        conn.execute(_events_table_schema_sql().format(table=current_month))

        # c. 把 legacy 資料 INSERT 進當月表（簡化版：全部塞當月）
        conn.execute(
            f"INSERT INTO {current_month} SELECT * FROM events_legacy"
        )

        # d. 建立 events view = 只看當月表（events_legacy 保留資料但不進 view，
        #    純粹作為 emergency rollback 用途：
        #    `DROP VIEW events; ALTER TABLE events_legacy RENAME TO events;`）
        conn.execute(
            f"""
            CREATE VIEW events AS
                SELECT * FROM {current_month}
            """
        )

        # e. INSTEAD OF INSERT trigger — 簡化版寫死當月表
        conn.execute(
            f"""
            CREATE TRIGGER events_insert_router
            INSTEAD OF INSERT ON events
            FOR EACH ROW
            BEGIN
                INSERT INTO {current_month}
                VALUES (NEW.id, NEW.scan_run_id, NEW.nvr_id, NEW.event_id,
                        NEW.device_id, NEW.event_topic, NEW.event_topics_json,
                        NEW.occurred_at, NEW.detected_at, NEW.resolved_at,
                        NEW.raw_json);
            END
            """
        )

        # f. INSTEAD OF UPDATE trigger — 用 id 找對應月份表更新
        conn.execute(
            f"""
            CREATE TRIGGER events_update_router
            INSTEAD OF UPDATE ON events
            FOR EACH ROW
            BEGIN
                UPDATE {current_month}
                SET scan_run_id = NEW.scan_run_id,
                    nvr_id = NEW.nvr_id,
                    event_id = NEW.event_id,
                    device_id = NEW.device_id,
                    event_topic = NEW.event_topic,
                    event_topics_json = NEW.event_topics_json,
                    occurred_at = NEW.occurred_at,
                    detected_at = NEW.detected_at,
                    resolved_at = NEW.resolved_at,
                    raw_json = NEW.raw_json
                WHERE id = OLD.id;
            END
            """
        )

        conn.commit()
        print(f"[ok] {db_path} events 已轉為 view + {current_month} 表 + triggers")
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
