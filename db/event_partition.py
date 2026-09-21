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
from datetime import date, datetime, timezone


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


def current_hot_tables(
    today: date | None = None, hot_window: int = 4
) -> list[str]:
    """傳回 today 起算 hot_window 個月（含當月）的分區表名清單。

    hot_window=4 → [當月, 上月, 上2月, 上3月]
    跨越年度時自動遞減月份並遞增年份（12 月 → 隔年 1 月）。

    Args:
        today: 計算基準日；None = 今天（UTC）
        hot_window: view 內含的月份數（含當月），預設 4

    Returns:
        list[str] 表名清單，由新到舊（[當月, 上月, ..., 最舊]）
    """
    today = today or datetime.now(timezone.utc).date()
    names = []
    for offset in range(hot_window):
        y, m = today.year, today.month - offset
        while m <= 0:
            m += 12
            y -= 1
        names.append(f"events_{y:04d}_{m:02d}")
    return names


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


def rebuild_events_view(
    conn: sqlite3.Connection,
    today: date | None = None,
    hot_window: int = 4,
) -> list[str]:
    """Week 4 #011：重建 events view = UNION ALL hot tables，並重建 INSTEAD OF triggers。

    必須在 _migrate_add_events_partition 之後（view 已存在）呼叫。
    Idempotent：可重複執行，每次都重建 view + 2 triggers（覆蓋舊定義）。

    Returns:
        實際寫進 view 的 hot tables 名單（已存在的實體表）。
    """
    today = today or datetime.now(timezone.utc).date()
    hot_names = current_hot_tables(today, hot_window)

    existing = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'events_2%'"
        ).fetchall()
    }
    hot = [t for t in hot_names if t in existing]
    if not hot:
        return []  # 沒有 hot tables；view 留原樣

    # 1. DROP 舊 view + 舊 triggers
    conn.execute("DROP VIEW IF EXISTS events")
    conn.execute("DROP TRIGGER IF EXISTS events_insert_router")
    conn.execute("DROP TRIGGER IF EXISTS events_update_router")

    # 2. CREATE 新 view = UNION ALL hot tables
    unions = " UNION ALL ".join(f"SELECT * FROM {t}" for t in hot)
    conn.execute(f"CREATE VIEW events AS {unions}")

    # 3. INSTEAD OF INSERT / UPDATE triggers 指向**當月表**（hot[0]）
    current = hot[0]
    conn.execute(
        f"""
        CREATE TRIGGER events_insert_router
        INSTEAD OF INSERT ON events
        FOR EACH ROW
        BEGIN
            INSERT INTO {current}
            VALUES (NEW.id, NEW.scan_run_id, NEW.nvr_id, NEW.camera_id,
                    NEW.event_id, NEW.device_id, NEW.event_topic,
                    NEW.event_topics_json, NEW.occurred_at, NEW.detected_at,
                    NEW.resolved_at, NEW.raw_json);
        END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER events_update_router
        INSTEAD OF UPDATE ON events
        FOR EACH ROW
        BEGIN
            UPDATE {current}
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
    return hot
