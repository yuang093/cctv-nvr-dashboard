"""Week 4 #011：events view 動態 UNION hot tables 測試。"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """建立含多張月份 events_YYYY_MM 表 + events view 的 DB（模擬 Week 3 套用後）。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    for y, m in [(2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
        conn.execute(
            f"""
            CREATE TABLE events_{y}_{m:02d} (
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
    # 預設 events view 指向當月表（Week 3 結束狀態）
    conn.execute(
        """
        CREATE VIEW events AS SELECT * FROM events_2026_10
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_view_unions_current_and_3_previous_months(fresh_db: str) -> None:
    """events view 應自動 UNION 當月 + 上 3 個月共 4 張熱表。"""
    from db.migrations.migrate_add_events_view_union import run as run_view_union

    run_view_union(fresh_db, today=date(2026, 10, 15))

    conn = sqlite3.connect(fresh_db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchall()
    assert len(rows) == 1

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchone()[0]

    # 4 張熱表都應該出現（10 月當月 + 9, 8, 7 月）
    assert "events_2026_07" in sql
    assert "events_2026_08" in sql
    assert "events_2026_09" in sql
    assert "events_2026_10" in sql
    # 6 月超出 hot window，不在 view
    assert "events_2026_06" not in sql
    conn.close()


def test_view_unions_with_custom_hot_window(tmp_path: Path) -> None:
    """hot_window=2 → view 只含當月 + 上 1 個月共 2 張熱表。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    for y, m in [(2026, 8), (2026, 9), (2026, 10)]:
        conn.execute(
            f"CREATE TABLE events_{y}_{m:02d} (id INTEGER PRIMARY KEY)"
        )
    # 預設 view 指向當月表
    conn.execute("CREATE VIEW events AS SELECT * FROM events_2026_10")
    conn.commit()
    conn.close()

    from db.migrations.migrate_add_events_view_union import run as run_view_union

    run_view_union(db_path, today=date(2026, 10, 15), hot_window=2)

    conn = sqlite3.connect(db_path)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchone()[0]
    assert "events_2026_09" in sql
    assert "events_2026_10" in sql
    assert "events_2026_08" not in sql
    conn.close()


def test_view_union_idempotent(fresh_db: str) -> None:
    """重複執行 view union migration 不應壞（[skip] 或 [ok] 皆可）。"""
    from db.migrations.migrate_add_events_view_union import run as run_view_union

    assert run_view_union(fresh_db, today=date(2026, 10, 15)) == 0
    assert run_view_union(fresh_db, today=date(2026, 10, 15)) == 0
    assert run_view_union(fresh_db, today=date(2026, 10, 15)) == 0

    # view 仍存在且內容正確
    conn = sqlite3.connect(fresh_db)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchone()[0]
    assert "events_2026_10" in sql
    conn.close()


def test_view_union_skips_when_events_not_a_view(tmp_path: Path) -> None:
    """若 events 仍是 table（Week 3 未跑）→ skip；不應該自動建 view。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    from db.migrations.migrate_add_events_view_union import run as run_view_union

    rc = run_view_union(db_path, today=date(2026, 10, 15))
    assert rc == 0  # 不視為失敗

    # view 不應該出現
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchall()
    assert len(rows) == 0
    conn.close()
