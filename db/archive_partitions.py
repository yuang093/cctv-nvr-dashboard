"""
db/archive_partitions.py
========================
Week 4 Issue #011：events 月分區整表歸檔核心邏輯。

冷資料層：對 hot window 外的分區表執行：
  1. Python sqlite3.iterdump() 取出該 table 的 CREATE + INSERT SQL
  2. gzip 壓縮成 events_YYYY_MM.sql.gz
  3. DROP TABLE
  4. VACUUM（釋放 SQLite 檔案實際磁碟空間）

設計選擇：使用 Python sqlite3.iterdump() 而非 sqlite3 CLI subprocess，
確保 Windows / Linux / macOS 三平台一致且無外部相依。
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from db.event_partition import current_hot_tables


def list_cold_partitions(
    db_path: str, today: date | None = None, hot_window: int = 4
) -> list[str]:
    """列出 hot window 外、且實際存在的分區表（給歸檔候選用）。

    回傳按時間由舊到新排序（[最舊, ..., 最新]），符合歸檔順序直覺。
    """
    today = today or datetime.now(timezone.utc).date()
    hot = set(current_hot_tables(today, hot_window))
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'events_2%' "
            "ORDER BY name"
        ).fetchall()
        # ORDER BY name ASC 已是時間順序（events_2026_05 < events_2026_06 ...）
        return [r[0] for r in rows if r[0] not in hot]
    finally:
        conn.close()


def dump_and_compress(
    db_path: str,
    table_name: str,
    archive_dir: str,
    suffix: str | None = None,
) -> str:
    """用 Python sqlite3.iterdump() 把 table 轉 SQL 並 gzip。

    Args:
        db_path: SQLite 檔路徑
        table_name: 要 dump 的 table 名稱
        archive_dir: 輸出 .sql.gz 的目錄
        suffix: 自訂檔名（None → "{table_name}.sql.gz"）
                e.g. "audit_log_2026-09-22.sql.gz" for Week 5 audit log rotation

    Returns: 產出的 .sql.gz 完整路徑
    """
    Path(archive_dir).mkdir(parents=True, exist_ok=True)
    archive_name = suffix or f"{table_name}.sql.gz"
    out_path = str(Path(archive_dir) / archive_name)

    conn = sqlite3.connect(db_path)
    try:
        # iterdump() 會傾印整個 DB schema + 所有資料；用「table filter」逐段過濾
        lines: list[str] = []
        for line in conn.iterdump():
            # 只保留與目標 table 相關的 SQL 行
            if (
                f'CREATE TABLE "{table_name}"' in line
                or f'CREATE TABLE {table_name}' in line
                or f'INSERT INTO "{table_name}"' in line
                or f'INSERT INTO {table_name}' in line
            ):
                lines.append(line)
        if not lines:
            raise RuntimeError(
                f"dump_and_compress: 從 iterdump() 找不到 {table_name} 的 SQL 行"
            )
    finally:
        conn.close()

    with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
        f.write("\n")
    return out_path


def drop_and_vacuum(db_path: str, table_name: str) -> None:
    """DROP TABLE 後跑 VACUUM（釋放 .db 檔案實際磁碟空間）。"""
    # 第一個連線：DROP TABLE
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()
    finally:
        conn.close()
    # 第二個連線：VACUUM（不能跟 DROP 同 transaction；VACUUM 必須是非 read tx）
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()


def run_archive_pass(
    db_path: str,
    archive_dir: str,
    today: date | None = None,
    hot_window: int = 4,
    keep_months: int = 0,
) -> dict:
    """跑一輪歸檔：識別 cold tables → dump+gzip → DROP+VACUUM。

    Args:
        db_path: SQLite 檔路徑
        archive_dir: .sql.gz 輸出目錄
        today: 計算基準日；None = 今天
        hot_window: view 內含的月份數（含當月）；預設 4
        keep_months: 即使超出 hot window 也要保留最近 N 個月不歸檔（安全緩衝；預設 0）

    Returns:
        dict 含 archived (gz 路徑 list)、dropped (table 名 list)、kept_in_view (table 名 list)
    """
    today = today or date.today()
    cold = list_cold_partitions(db_path, today, hot_window)

    # 安全緩衝：保留最新 N 個月 cold 不歸檔（cold 已是時間正序 → 前段最舊、後段最新）
    if keep_months > 0 and len(cold) > keep_months:
        archived_targets = cold[:-keep_months] if keep_months > 0 else cold
        kept_cold = cold[-keep_months:]
    else:
        archived_targets = cold
        kept_cold = []

    gz_paths: list[str] = []
    dropped: list[str] = []
    for tbl in archived_targets:
        gz = dump_and_compress(db_path, tbl, archive_dir)
        gz_paths.append(gz)
        drop_and_vacuum(db_path, tbl)
        dropped.append(tbl)

    kept_in_view = [
        t
        for t in current_hot_tables(today, hot_window)
        if Path(db_path).exists() and _table_exists(db_path, t)
    ]
    return {
        "archived": gz_paths,
        "dropped": dropped,
        "kept_in_view": kept_in_view,
        "kept_cold_for_safety": kept_cold,
        "today": today.isoformat(),
        "hot_window": hot_window,
        "keep_months": keep_months,
    }


def _table_exists(db_path: str, table_name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
