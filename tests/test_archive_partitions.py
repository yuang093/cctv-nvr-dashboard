"""Week 4 #011：events 月分區整表歸檔測試。"""
from __future__ import annotations

import gzip
import sqlite3
from datetime import date
from pathlib import Path

import pytest


@pytest.fixture
def multi_month_db(tmp_path: Path) -> str:
    """建立含 6 張月份分區表的 DB（模擬長期運行的 production）。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    # 建立 6 個月分區（包含當月 10 月）
    for y, m in [(2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
        conn.execute(
            f"""
            CREATE TABLE events_{y}_{m:02d} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_run_id INTEGER NOT NULL,
                nvr_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                event_topic TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                resolved_at TEXT NULL
            )
            """
        )
        # 塞 2 筆資料
        conn.execute(
            f"INSERT INTO events_{y}_{m:02d} "
            f"(scan_run_id, nvr_id, device_id, event_topic, occurred_at, resolved_at) "
            f"VALUES (1, 1, 'cam-1', 'VIDEO_LOSS', '2026-{m:02d}-01T00:00:00Z', NULL)"
        )
        conn.execute(
            f"INSERT INTO events_{y}_{m:02d} "
            f"(scan_run_id, nvr_id, device_id, event_topic, occurred_at, resolved_at) "
            f"VALUES (2, 1, 'cam-2', 'TAMPERING', '2026-{m:02d}-15T12:00:00Z', '2026-{m:02d}-16T00:00:00Z')"
        )
    conn.commit()
    conn.close()
    return db_path


def test_list_cold_partitions_excludes_hot_window(multi_month_db: str) -> None:
    """hot_window=4 → 5,6 月是 cold；7-10 月在 hot window。"""
    from db.archive_partitions import list_cold_partitions

    cold = list_cold_partitions(multi_month_db, today=date(2026, 10, 15), hot_window=4)
    assert "events_2026_05" in cold
    assert "events_2026_06" in cold
    assert "events_2026_07" not in cold
    assert "events_2026_08" not in cold
    assert "events_2026_09" not in cold
    assert "events_2026_10" not in cold
    # 按時間正序
    assert cold == ["events_2026_05", "events_2026_06"]


def test_dump_and_compress_creates_gz(multi_month_db: str, tmp_path: Path) -> None:
    """dump_and_compress 產 .sql.gz 檔，含 CREATE + INSERT。"""
    from db.archive_partitions import dump_and_compress

    out_dir = tmp_path / "archives"
    gz_path = dump_and_compress(multi_month_db, "events_2026_05", str(out_dir))

    assert gz_path.endswith("events_2026_05.sql.gz")
    assert Path(gz_path).exists()
    with gzip.open(gz_path, "rt") as f:
        content = f.read()
    assert "CREATE TABLE events_2026_05" in content or "events_2026_05" in content
    assert "INSERT INTO" in content and "events_2026_05" in content
    assert "VIDEO_LOSS" in content
    assert "TAMPERING" in content


def test_drop_and_vacuum_removes_table(multi_month_db: str) -> None:
    """DROP 後表不存在；VACUUM 不報錯。"""
    from db.archive_partitions import drop_and_vacuum

    drop_and_vacuum(multi_month_db, "events_2026_05")

    conn = sqlite3.connect(multi_month_db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events_2026_05'"
        ).fetchall()
        assert len(rows) == 0
    finally:
        conn.close()


def test_run_archive_pass_end_to_end(multi_month_db: str, tmp_path: Path) -> None:
    """整輪歸檔：cold 兩個月被歸檔+drop；hot 4 個月保留。"""
    from db.archive_partitions import run_archive_pass

    archive_dir = tmp_path / "archives"
    summary = run_archive_pass(
        db_path=multi_month_db,
        archive_dir=str(archive_dir),
        today=date(2026, 10, 15),
        hot_window=4,
        keep_months=0,
    )

    assert set(summary["dropped"]) == {"events_2026_05", "events_2026_06"}
    assert set(summary["kept_in_view"]) == {
        "events_2026_07",
        "events_2026_08",
        "events_2026_09",
        "events_2026_10",
    }
    # archive 檔存在
    assert (archive_dir / "events_2026_05.sql.gz").exists()
    assert (archive_dir / "events_2026_06.sql.gz").exists()

    # DB 內只剩 hot 4 個月 + events_legacy 不存在（這個 fixture 沒建 legacy）
    conn = sqlite3.connect(multi_month_db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'events_%' "
            "ORDER BY name"
        ).fetchall()
        names = [r[0] for r in rows]
        assert "events_2026_05" not in names
        assert "events_2026_06" not in names
        assert "events_2026_07" in names
        assert "events_2026_10" in names
    finally:
        conn.close()


def test_keep_months_safety_buffer(multi_month_db: str, tmp_path: Path) -> None:
    """keep_months=1 → 即使 6 月超出 hot window 仍保留不歸檔（安全緩衝）。"""
    from db.archive_partitions import run_archive_pass

    archive_dir = tmp_path / "archives"
    summary = run_archive_pass(
        db_path=multi_month_db,
        archive_dir=str(archive_dir),
        today=date(2026, 10, 15),
        hot_window=4,
        keep_months=1,
    )

    # 6 月超出 hot window 但被 keep_months 保留 → 不歸檔
    # 5 月是最舊唯一 cold → 歸檔
    assert summary["dropped"] == ["events_2026_05"]
    assert summary["kept_cold_for_safety"] == ["events_2026_06"]
