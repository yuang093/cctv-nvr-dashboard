"""
db/migrations/migrate_add_events_view_union.py
=============================================
Week 4 Issue #011：events view 改為動態 UNION 4 張熱表（hot tables）。

使用：
    python db/migrations/migrate_add_events_view_union.py [db_path]

退出碼：0 = 已套用（或 skip）/ 1 = 失敗

策略：
  1. 確認 events 是 view（Week 3 必須已跑 partition migration；否則 skip）
  2. 計算 hot tables 名單（current + 過去 hot_window-1 個月）
  3. 篩選實際存在於 DB 的 hot tables
  4. DROP VIEW events + CREATE VIEW events AS SELECT * FROM t1 UNION ALL ... UNION ALL SELECT * FROM tN

對應 inline 邏輯：db.sqlite_writer._init_schema 在 _migrate_add_events_partition 之後呼叫。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

from db.event_partition import rebuild_events_view


def run(db_path: str, today: date | None = None, hot_window: int = 4) -> int:
    conn = sqlite3.connect(db_path)
    try:
        # ── 檢查 events 是否為 view（Week 3 已轉換才會是 view） ──
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='events'"
        ).fetchone()
        if row is None:
            print(f"[skip] {db_path} 沒有 events 物件（尚未初始化）")
            return 0
        if row[0] != "view":
            print(f"[skip] {db_path} events 不是 view（Week 3 partition 未套用）")
            return 0

        # ── 委派給共用 helper（包含 view + triggers 重建） ──
        hot = rebuild_events_view(conn, today=today, hot_window=hot_window)
        conn.commit()
        if not hot:
            print(f"[skip] {db_path} 找不到任何 hot table")
            return 0
        print(f"[ok] {db_path} events view 重建 → {hot}")
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
