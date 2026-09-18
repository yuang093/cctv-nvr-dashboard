"""
db/event_partition.py
=====================
Week 3 Issue #009：應用層 partition 輔助函式。

Phase 1 不直接呼叫（trigger 已處理 INSERT/UPDATE 路由），
但保留給未來：
  - 月底 cron 建下月分區表（ensure_next_month_partition）
  - 維運指令（手動查詢當月是哪個表）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def current_month_table_name(now: datetime | None = None) -> str:
    """當月分區表名，例如 'events_2026_09'。"""
    now = now or datetime.now(timezone.utc)
    return f"events_{now.year:04d}_{now.month:02d}"


def next_month_table_name(now: datetime | None = None) -> str:
    """下月分區表名。"""
    now = now or datetime.now(timezone.utc)
    if now.month == 12:
        return f"events_{now.year + 1:04d}_01"
    return f"events_{now.year:04d}_{now.month + 1:02d}"


def ensure_next_month_partition(conn: sqlite3.Connection) -> str:
    """確保下月分區表存在（給月底 cron 呼叫）。
    Returns the table name created (or already existing).

    Note: 單純建表，尚未自動納入 events view。
    Phase 1 簡化：view = 當月表；多月份 view UNION ALL 留給 Phase 2 歸檔時一起處理。
    """
    table = next_month_table_name()
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_run_id INTEGER NOT NULL,
            nvr_id INTEGER NOT NULL,
            camera_id INTEGER NULL,
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
    )
    conn.commit()
    return table
