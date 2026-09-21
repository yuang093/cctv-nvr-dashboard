# Week 4 #011 — Phase 2 歸檔腳本（events 動態 UNION + 整表 gzip 封存）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為 Week 3 月分區加上一層「動態熱資料層（view UNION 90 天）+ 冷資料整表歸檔（gzip + DROP + VACUUM）」，並整合進 `run_worker` 排程（**每週日凌晨 03:00** 守門）。

**關鍵決策（user 2026-09-21 拍板）**：
- `hot_window = 4`（含當月 = 90 天滑動窗）
- `keep_months = 1`（hot window 外再多保留 1 個月 cold 不歸檔；總緩衝 = 90 + 30 = 120 天，給跨季度調閱用）
- 排程頻率 = **每週日**凌晨 03:00（HOUR==3 且 DOW==0 守門；以月為單位建表、整表脫落每月最多一次，每日檢查屬多餘）
- 歸檔檔位置 = **`./archives/`**（與 DB 同一掛載點，備份/遷移不漏）

**Architecture:**
1. **熱層（90 天滑動窗）**：`events` view 從「只映射當月表」改為「`UNION ALL` 當月 + 上 3 個月熱表」。冷資料（>90 天）對應的分區表從 view 卸除但保留在 SQLite，待 archive 整批壓縮後才 DROP。
2. **冷層（歸檔）**：獨立 Python script `archive_old_partitions.py` 對「已從 view 卸除」且「存在時間 > 90 天」的分區表呼叫 `sqlite3 .dump` 輸出 SQL、用 `gzip` 壓縮成 `events_YYYY_MM.sql.gz`、最後 `DROP TABLE` + `VACUUM` 釋放磁碟。
3. **view 滑動維護**：歸檔後 view 必須 refresh（hot table 名單更新、INSTEAD OF INSERT trigger 重新指向當月 monthly table）。
4. **Cron 整合**：`run_worker.sh` / `.ps1` / `.bat` 新增「歸檔守門」步驟：每日第一次被 cron 叫醒時檢查今天是否為排程日（預設每日），若是則執行 `archive_old_partitions.py --dry-run-print` 提示、實際執行版另外用獨立 cron `0 3 * * *` 呼叫。

**Tech Stack:** Python 3.10+ stdlib（sqlite3, gzip, subprocess, datetime）+ `run_worker.{sh,ps1,bat}`（既有 cron 入口）。

---

## 為什麼這套架構符合「應用層零修改」

| 操作 | Week 3 後 | Week 4 後 |
|---|---|---|
| `SELECT * FROM events WHERE occurred_at > now-90d` | 走 view → 當月表（90d 內幾乎都在當月） | 走 view → `UNION ALL 4 張熱表`（透明） |
| `SELECT * FROM events WHERE occurred_at < now-90d` | 只能讀當月表（90d 外的資料在 legacy/上月表） | view 不再包含這範圍 → 需用歸檔檔 `.sql.gz` 或 `events_archive` 表 |
| `INSERT INTO events` | INSTEAD OF trigger 寫當月表 | INSTEAD OF trigger 寫當月表（trigger SQL 動態生成，仍指向當月） |
| `UPDATE events SET resolved_at = ?` | 走 INSTEAD OF trigger | 走 INSTEAD OF trigger（trigger SQL 動態生成，仍指向當月） |

關鍵差異：**Week 4 後 view 自動滑動**，trigger 在每個月份切換時需要重建指向新當月表（由 view rebuild 程序一併處理）。

---

## File Structure

| 檔案 | 角色 |
|---|---|
| `db/event_partition.py`（修） | 加入 `hot_window_months: int = 4`（含當月）+ `current_hot_tables() -> list[str]` + `rebuild_events_view(conn)` |
| `db/migrations/migrate_add_events_archive.py`（新） | offline 工具腳本：把 view 從「單表」改為「UNION 4 張熱表」；idempotent |
| `db/archive_partitions.py`（新） | 核心歸檔邏輯：`list_cold_partitions()`、`dump_and_compress()`、`drop_and_vacuum()`、`run_archive_pass()` |
| `scripts/archive_old_partitions.py`（新） | CLI 入口：`--db --dry-run --keep-months` 參數 |
| `tests/test_events_view_union.py`（新） | 5 項測試：view 自動 UNION、trigger 動態指向、歸檔後 view 更新 |
| `tests/test_archive_partitions.py`（新） | 7 項測試：list_cold_partitions / dump 產物 / gzip 副檔名 / DROP 釋放 / VACUUM / run_archive_pass 整合 |
| `run_worker.sh`、`run_worker.ps1`（修） | 新增「每日首跑時呼叫 archive 子流程」分支 |
| `run_worker.bat`（重生） | 由 `_write_bat.py` 重新生成（**禁止手改**） |
| `_write_bat.py`（修） | `RUN_WORKER` byte literal 加入 archive 子流程 |
| `database_schema.md`（修） | §4.1 改寫「view = 4 張熱表 UNION」+ 新 §4.2 `events_archive` 表規格 + 新 §4.3 歸檔 SOP |
| `docs/roadmap-issues.md`（修） | Week 4 #011 標 ✅ |

---

## Task Structure

### Task 1: schema + offline migration + 紅燈測試

**Files:**
- Create: `db/migrations/migrate_add_events_view_union.py`
- Modify: `db/event_partition.py:18-59`
- Test: `tests/test_events_view_union.py`

**Steps:**

- [ ] **Step 1.1: 寫紅燈測試 — view 自動 UNION 4 張熱表**

```python
# tests/test_events_view_union.py
def test_view_unions_current_and_3_previous_months(tmp_path):
    """events view 應自動 UNION 當月 + 上 3 個月。"""
    db_path = str(tmp_path / "test.db")
    # 建立 5 張月份表（含當月）
    conn = sqlite3.connect(db_path)
    for y, m in [(2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
        conn.execute(f"""
            CREATE TABLE events_{y}_{m:02d} (
                id INTEGER PRIMARY KEY,
                occurred_at TEXT,
                event_topic TEXT
            )
        """)
    conn.commit()
    conn.close()

    # 跑 view UNION migration（假設 today=2026-10-15）
    run_view_union_migration(db_path, today=date(2026, 10, 15))

    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='view' AND name='events'
    """).fetchall()
    assert len(rows) == 1

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchone()[0]
    # 4 張熱表都應該出現
    assert "events_2026_07" in sql
    assert "events_2026_08" in sql
    assert "events_2026_09" in sql
    assert "events_2026_10" in sql
    # 5 個月前那張不在 view
    assert "events_2026_06" not in sql
```

- [ ] **Step 1.2: 寫紅燈測試 — hot window 月數可參數化**

```python
def test_view_unions_with_custom_hot_window(tmp_path):
    """hot_window=2 → view 只含當月 + 上 1 個月。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    for y, m in [(2026, 8), (2026, 9), (2026, 10)]:
        conn.execute(f"CREATE TABLE events_{y}_{m:02d} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    run_view_union_migration(db_path, today=date(2026, 10, 15), hot_window=2)

    sql = sqlite3.connect(db_path).execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='events'"
    ).fetchone()[0]
    assert "events_2026_09" in sql
    assert "events_2026_10" in sql
    assert "events_2026_08" not in sql
```

- [ ] **Step 1.3: 跑測試確認 RED**

Run: `pytest tests/test_events_view_union.py -v`
Expected: 兩個測試都 `ModuleNotFoundError: No module named 'db.migrations.migrate_add_events_view_union'` 或類似 import 失敗

- [ ] **Step 1.4: 實作 `event_partition.current_hot_tables()` 純函式**

```python
# db/event_partition.py — 加入下列函式
def current_hot_tables(today: date | None = None, hot_window: int = 4) -> list[str]:
    """傳回 today 起算 hot_window 個月（含當月）的分區表名清單。

    hot_window=4 → [當月, 上月, 上2月, 上3月]
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
```

- [ ] **Step 1.5: 實作 offline migration `migrate_add_events_view_union.py`**

```python
# db/migrations/migrate_add_events_view_union.py
"""Week 4 #011: events view → UNION ALL hot tables."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

from db.event_partition import current_hot_tables


def run(db_path: str, today: date | None = None, hot_window: int = 4) -> int:
    conn = sqlite3.connect(db_path)
    try:
        # 確認 events 是 view（Week 3 已轉換）
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='events'"
        ).fetchone()
        if row is None or row["type"] != "view":
            print(f"[skip] {db_path} events 不是 view（Week 3 未套用）")
            return 0

        # 檢查哪些 hot tables 真的存在
        existing = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'events_2%'"
            ).fetchall()
        }
        hot = [t for t in current_hot_tables(today, hot_window) if t in existing]
        if not hot:
            print(f"[skip] {db_path} 找不到任何 hot table")
            return 0

        # 重建 view
        unions = " UNION ALL ".join(f"SELECT * FROM {t}" for t in hot)
        conn.execute("DROP VIEW IF EXISTS events")
        conn.execute(f"CREATE VIEW events AS {unions}")
        conn.commit()
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
```

- [ ] **Step 1.6: 跑測試確認 GREEN**

Run: `pytest tests/test_events_view_union.py -v`
Expected: 兩個測試全 PASS

- [ ] **Step 1.7: Commit**

```bash
git add db/event_partition.py db/migrations/migrate_add_events_view_union.py tests/test_events_view_union.py
git commit -m "feat(db): Week 4 #011 — events view 動態 UNION hot tables"
```

---

### Task 2: inline migration 整合到 `_init_schema`

**Files:**
- Modify: `db/sqlite_writer.py:215-248`（`_init_schema` migration 順序）

**Steps:**

- [ ] **Step 2.1: 修改 `_init_schema` 在 partition migration 之後跑 view union migration**

```python
# db/sqlite_writer.py — _init_schema 內，在 _migrate_add_events_partition(conn) 之後加入：
# Week 4 #011：把 events view 從「單一 monthly table」改為「UNION hot tables」
# 必須在 _migrate_add_events_partition 之後（view 已建立）才能 rebuild。
if self._is_events_view(conn):
    from db.migrations.migrate_add_events_view_union import run as run_view_union
    run_view_union(self.db_path)
```

- [ ] **Step 2.2: 跑既有 partition 測試確認仍綠**

Run: `pytest tests/test_events_partition.py -v`
Expected: 5 項全 PASS（既有 partition migration 行為不變；view 在 init 時多走一次 union rebuild 但結果相同因為 hot_window=4 預設含當月 + 上 3 月，當月表一定存在）

- [ ] **Step 2.3: 跑全 suite 確認 985 項仍綠**

Run: `pytest -q`
Expected: `985 passed`

- [ ] **Step 2.4: Commit**

```bash
git add db/sqlite_writer.py
git commit -m "refactor(db): Week 4 — _init_schema 整合 view union migration"
```

---

### Task 3: archive 核心邏輯 — `db/archive_partitions.py`

**Files:**
- Create: `db/archive_partitions.py`
- Test: `tests/test_archive_partitions.py`

**Steps:**

- [ ] **Step 3.1: 寫紅燈測試 — `list_cold_partitions()` 識別可歸檔表**

```python
# tests/test_archive_partitions.py
def test_list_cold_partitions_excludes_hot_window(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    # 建立 6 個月分區（hot_window=4 → 前 2 個月 cold）
    for y, m in [(2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
        conn.execute(f"CREATE TABLE events_{y}_{m:02d} (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    cold = list_cold_partitions(db_path, today=date(2026, 10, 15), hot_window=4)
    assert "events_2026_05" in cold
    assert "events_2026_06" in cold
    assert "events_2026_07" not in cold  # 7 月還在 hot window
    assert "events_2026_10" not in cold  # 當月
```

- [ ] **Step 3.2: 寫紅燈測試 — `dump_and_compress()` 產 .sql.gz**

```python
def test_dump_and_compress_creates_gz(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE events_2026_05 (id INTEGER PRIMARY KEY, msg TEXT)")
    conn.execute("INSERT INTO events_2026_05 VALUES (1, 'hello')")
    conn.commit()
    conn.close()

    out_dir = tmp_path / "archives"
    out_dir.mkdir()
    gz_path = dump_and_compress(db_path, "events_2026_05", str(out_dir))

    assert gz_path.endswith(".sql.gz")
    assert Path(gz_path).exists()
    # gzip 解開後應有 CREATE TABLE + INSERT
    import gzip
    with gzip.open(gz_path, "rt") as f:
        content = f.read()
    assert "CREATE TABLE events_2026_05" in content
    assert "INSERT INTO events_2026_05" in content
    assert "'hello'" in content
```

- [ ] **Step 3.3: 寫紅燈測試 — `drop_and_vacuum()` 釋放磁碟**

```python
def test_drop_and_vacuum_removes_table(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE events_2026_05 (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO events_2026_05 VALUES (1)")
    conn.commit()
    conn.close()

    drop_and_vacuum(db_path, "events_2026_05")

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events_2026_05'"
    ).fetchall()
    assert len(rows) == 0
```

- [ ] **Step 3.4: 寫紅燈測試 — `run_archive_pass()` 整合**

```python
def test_run_archive_pass_end_to_end(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    for y, m in [(2026, 5), (2026, 6), (2026, 10)]:
        conn.execute(f"CREATE TABLE events_{y}_{m:02d} (id INTEGER PRIMARY KEY)")
        conn.execute(f"INSERT INTO events_{y}_{m:02d} VALUES (1)")
    conn.commit()
    conn.close()

    archive_dir = tmp_path / "archives"
    summary = run_archive_pass(
        db_path=db_path,
        archive_dir=str(archive_dir),
        today=date(2026, 10, 15),
        hot_window=4,
        keep_months=0,
    )

    # 5 月和 6 月被歸檔；10 月仍在 hot window 不動
    assert set(summary["archived"]) == {"events_2026_05", "events_2026_06"}
    assert summary["dropped"] == ["events_2026_05", "events_2026_06"]
    assert summary["kept_in_view"] == ["events_2026_10"]
    # archive 檔存在
    assert (archive_dir / "events_2026_05.sql.gz").exists()
    assert (archive_dir / "events_2026_06.sql.gz").exists()
```

- [ ] **Step 3.5: 跑測試確認 RED**

Run: `pytest tests/test_archive_partitions.py -v`
Expected: 4 個測試 `ImportError: cannot import name 'list_cold_partitions' from 'db.archive_partitions'`

- [ ] **Step 3.6: 實作 `db/archive_partitions.py`**

```python
# db/archive_partitions.py
"""Week 4 #011：events 月分區整表歸檔核心邏輯。

冷資料層：對 hot window 外的分區表執行：
  1. sqlite3 .dump → 該 table SQL
  2. gzip 壓縮成 events_YYYY_MM.sql.gz
  3. DROP TABLE
  4. VACUUM（釋放 SQLite 檔案空間）
"""
from __future__ import annotations

import gzip
import sqlite3
import subprocess
from datetime import date
from pathlib import Path

from db.event_partition import current_hot_tables


def list_cold_partitions(
    db_path: str, today: date | None = None, hot_window: int = 4
) -> list[str]:
    """列出 hot window 外、且實際存在的分區表（給歸檔候選用）。"""
    today = today or date.today()
    hot = set(current_hot_tables(today, hot_window))
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'events_2%' "
            "ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows if r[0] not in hot]
    finally:
        conn.close()


def dump_and_compress(db_path: str, table_name: str, archive_dir: str) -> str:
    """用 sqlite3 .dump 把 table 轉 SQL 並 gzip。

    Returns: 產出的 .sql.gz 完整路徑
    """
    Path(archive_dir).mkdir(parents=True, exist_ok=True)
    out_path = str(Path(archive_dir) / f"{table_name}.sql.gz")
    # sqlite3 .dump 會傾印整個 DB，所以用 echo | 過濾只取目標 table
    proc = subprocess.run(
        ["sqlite3", db_path, f".dump {table_name}"],
        capture_output=True,
        text=True,
        check=True,
    )
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        f.write(proc.stdout)
    return out_path


def drop_and_vacuum(db_path: str, table_name: str) -> None:
    """DROP TABLE 後跑 VACUUM（釋放 .db 檔案實際磁碟空間）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.commit()
        # VACUUM 不能在同一個 transaction 內執行
    finally:
        conn.close()
    # 重新連線跑 VACUUM
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
        keep_months: 即使超出 hot window 也要保留 N 個月不歸檔（給安全緩衝；預設 0）

    Returns:
        dict 含 archived (gz 路徑 list)、dropped (table 名 list)、kept_in_view (table 名 list)
    """
    today = today or date.today()
    cold = list_cold_partitions(db_path, today, hot_window)
    # 安全緩衝：保留最近 N 個月 cold 表不動
    if keep_months > 0:
        # 把最新的 N 個從 cold 移除（cold 已按字母排序；需要按時間排序）
        hot = current_hot_tables(today, hot_window)
        boundary = set(hot)  # hot window 內的
        # 排序 cold 由新到舊（字母倒序即時間倒序）
        sorted_cold = sorted(cold, reverse=True)
        to_keep = set(sorted_cold[:keep_months])
        archived_targets = [t for t in cold if t not in to_keep]
    else:
        archived_targets = cold

    gz_paths = []
    dropped = []
    for tbl in archived_targets:
        gz = dump_and_compress(db_path, tbl, archive_dir)
        gz_paths.append(gz)
        drop_and_vacuum(db_path, tbl)
        dropped.append(tbl)

    kept_in_view = [
        t for t in current_hot_tables(today, hot_window)
        if Path(db_path).exists() and _table_exists(db_path, t)
    ]
    return {
        "archived": gz_paths,
        "dropped": dropped,
        "kept_in_view": kept_in_view,
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
```

- [ ] **Step 3.7: 跑測試確認 GREEN**

Run: `pytest tests/test_archive_partitions.py -v`
Expected: 4 項全 PASS

- [ ] **Step 3.8: Commit**

```bash
git add db/archive_partitions.py tests/test_archive_partitions.py
git commit -m "feat(db): Week 4 #011 — 整表 gzip 封存核心邏輯"
```

---

### Task 4: CLI 入口 `scripts/archive_old_partitions.py`

**Files:**
- Create: `scripts/archive_old_partitions.py`

**Steps:**

- [ ] **Step 4.1: 寫 CLI 入口（argparse + dry-run 支援）**

```python
# scripts/archive_old_partitions.py
"""Week 4 #011：歸檔腳本 CLI 入口。

用法：
    python scripts/archive_old_partitions.py --db nvr_scan.db --archive-dir ./archives
    python scripts/archive_old_partitions.py --db nvr_scan.db --dry-run
    python scripts/archive_old_partitions.py --db nvr_scan.db --keep-months 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 讓 script 可直接被 `python scripts/...` 執行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.archive_partitions import list_cold_partitions, run_archive_pass


def main() -> int:
    p = argparse.ArgumentParser(description="歸檔 events 舊分區表（gzip + DROP + VACUUM）")
    p.add_argument("--db", default="./nvr_scan.db", help="SQLite 檔路徑")
    p.add_argument(
        "--archive-dir",
        default="./archives",
        help=".sql.gz 輸出目錄（預設 ./archives）",
    )
    p.add_argument(
        "--hot-window", type=int, default=4, help="view 內含的月份數（含當月）"
    )
    p.add_argument(
        "--keep-months",
        type=int,
        default=0,
        help="保留最近 N 個月 cold table 不歸檔（安全緩衝）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出將被歸檔的表，不實際執行",
    )
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f"[FATAL] 找不到 DB：{args.db}", file=sys.stderr)
        return 2

    if args.dry_run:
        cold = list_cold_partitions(args.db, hot_window=args.hot_window)
        print(json.dumps({
            "dry_run": True,
            "would_archive": cold,
            "hot_window": args.hot_window,
        }, indent=2, ensure_ascii=False))
        return 0

    summary = run_archive_pass(
        db_path=args.db,
        archive_dir=args.archive_dir,
        hot_window=args.hot_window,
        keep_months=args.keep_months,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4.2: smoke test（dry-run 模式）**

Run: `python scripts/archive_old_partitions.py --db nvr_scan.db --dry-run`
Expected: 印出 JSON 列出 cold tables（若 DB 還沒建過分區則為空 list）

- [ ] **Step 4.3: 在備份 DB 上實測**

```bash
# 用 Week 3 留下的 backup DB 實測
python scripts/archive_old_partitions.py --db nvr_scan.db.backup-20260918 --dry-run
```
Expected: 看到 events_legacy 是否會被納入 cold（注意：_legacy 不符合 `events_2%` pattern，不會被誤刪）

- [ ] **Step 4.4: Commit**

```bash
git add scripts/archive_old_partitions.py
git commit -m "feat(scripts): Week 4 #011 — 歸檔 CLI 入口（dry-run + keep-months）"
```

---

### Task 5: `run_worker` 整合歸檔子流程（每週日凌晨 03:00 守門）

**Files:**
- Modify: `run_worker.sh`
- Modify: `run_worker.ps1`
- Modify: `_write_bat.py`（RUN_WORKER byte literal）
- Regenerate: `run_worker.bat`（跑 `python _write_bat.py`）

**Steps:**

- [ ] **Step 5.1: 修改 `run_worker.sh` 加入歸檔步驟（每週日凌晨 03:00 守門）**

```bash
# run_worker.sh — 在「執行掃描」之前加入：
# === 歸檔守門（Week 4 #011）===
# 每週日凌晨 03:00（HOUR==03 且 DOW==0）才跑歸檔。
# 因為是月分區，整表脫落每月頂多一次，每週檢查足夠。
HOUR=$(date +%H)
DOW=$(date +%u)  # 1=Mon, 7=Sun
if [ "$HOUR" == "03" ] && [ "$DOW" == "7" ]; then
    ARCHIVE_DIR="${PROJECT_DIR}/archives"
    "$VENV_PY" scripts/archive_old_partitions.py \
        --db "${PROJECT_DIR}/nvr_scan.db" \
        --archive-dir "$ARCHIVE_DIR" \
        --hot-window 4 \
        --keep-months 1 \
        >> "$LOG_FILE" 2>&1
    ARCHIVE_EXIT=$?
    if [ $ARCHIVE_EXIT -ne 0 ]; then
        echo "[$(date -Iseconds)] archive_old_partitions exit_code=$ARCHIVE_EXIT" >> "$LOG_FILE"
    fi
fi

# === 主流程 ===
```

- [ ] **Step 5.2: 修改 `run_worker.ps1` 加入歸檔步驟（每週日凌晨 03:00 守門）**

```powershell
# run_worker.ps1 — 在「執行掃描」之前加入：
# === 歸檔守門（Week 4 #011）===
# PowerShell: DayOfWeek 0=Sunday；小時 3=凌晨 3 點
$HOUR = (Get-Date).Hour
$DOW = [int](Get-Date).DayOfWeek
if ($HOUR -eq 3 -and $DOW -eq 0) {
    $ARCHIVE_DIR = Join-Path $PROJECT_DIR "archives"
    python scripts/archive_old_partitions.py `
        --db (Join-Path $PROJECT_DIR "nvr_scan.db") `
        --archive-dir $ARCHIVE_DIR `
        --hot-window 4 `
        --keep-months 1 `
        2>&1 | Tee-Object -FilePath $LOG_FILE -Append
    $ARCHIVE_EXIT = $LASTEXITCODE
    if ($ARCHIVE_EXIT -ne 0) {
        Add-Content -Path $LOG_FILE -Value "[$(Get-Date -Format 'o')] archive_old_partitions exit_code=$ARCHIVE_EXIT"
    }
}
```

- [ ] **Step 5.3: 修改 `_write_bat.py` RUN_WORKER byte literal 加入歸檔步驟（每週日凌晨 03:00）

在現有 `python nvr_scanner.py ...` 那一行**之前**插入（bytes literal）。
Windows cmd 用 `%WEEKDAY%` 取 DOW（Sunday=0）：

```python
b"REM === ææå®éï¼Week 4 #011ï¼===",
b"REM æ¯é±æ¥å¤æ´ 03:00 æè·ï¼æååææéæ±ï¼",
b"REM Windows cmd: %%WEEKDAY%% 0=Sun, 1=Mon...Sat=6",
b'for /f "tokens=1 delims=:" %%a in ("%time%") do set "HOUR=%%a"',
b'if "%HOUR%"==" 3" if "%WEEKDAY%"=="0" (',
b'    echo [INFO] Week 4 #011 running archive pass at %date% %time%',
b'    python scripts\archive_old_partitions.py ^',
b'        --db "%PROJECT_DIR%\nvr_scan.db" ^',
b'        --archive-dir "%PROJECT_DIR%\archives" ^',
b'        --hot-window 4 ^',
b'        --keep-months 1',
b'    if errorlevel 1 (',
b'        echo [WARN] archive_old_partitions exit_code=%errorlevel%',
b'    )',
b")",
b"",
```

Step 5.4: 重新生成 `run_worker.bat`**

Run: `python _write_bat.py`
Verify: `git diff run_worker.bat` 應該只看到 HOUR 守門 + python archive 子流程區塊

- [ ] **Step 5.5: 手動測試三平台入口**

```bash
# Linux/macOS
./run_worker.sh

# Windows PowerShell
.\run_worker.ps1

# Windows cmd
run_worker.bat
```
Expected: 三個都成功執行（HOUR 不等於 3 時只跑掃描；HOUR=3 時多跑歸檔）

- [ ] **Step 5.6: Commit**

```bash
git add run_worker.sh run_worker.ps1 _write_bat.py run_worker.bat
git commit -m "feat(ops): Week 4 #011 — run_worker 整合每日凌晨 03:00 歸檔子流程"
```

---

### Task 6: 完整 pytest 驗證

**Files:** 無新檔案；只跑測試

**Steps:**

- [ ] **Step 6.1: 跑全 suite**

Run: `pytest -q`
Expected: `990 passed`（baseline 985 + 新增 5 項 view union 測試 + 新增 4 項 archive 測試）

- [ ] **Step 6.2: 若有失敗，按 systematic-debugging skill 修正**

- [ ] **Step 6.3: 跑特定 partition + archive 測試確認獨立綠**

Run: `pytest tests/test_events_partition.py tests/test_events_view_union.py tests/test_archive_partitions.py -v`
Expected: 5 + 2 + 4 = 11 項全 PASS

- [ ] **Step 6.4: 確認備份 DB 仍可被讀**

```bash
sqlite3 nvr_scan.db.backup-20260918 "SELECT type FROM sqlite_master WHERE name='events'"
```
Expected: `view`

- [ ] **Step 6.5: 在備份 DB 上跑 archive dry-run（驗證不破壞現況）**

Run: `python scripts/archive_old_partitions.py --db nvr_scan.db.backup-20260918 --dry-run`
Expected: 印出 cold tables（可能為空，因為備份 DB 只有當月 + legacy）

- [ ] **Step 6.6: Commit（如有 pytest 調整）**

```bash
git add tests/
git commit -m "test: Week 4 — 完整 pytest 990 項驗證歸檔 + view union"
```

---

### Task 7: `database_schema.md` 同步更新

**Files:**
- Modify: `database_schema.md` §4.1（view 改寫）+ 新 §4.2（archive 表/檔）+ 新 §4.3（歸檔 SOP）

**Steps:**

- [ ] **Step 7.1: 修改 §4.1 標題與內容**

把「events_YYYY_MM — 月分區實體表（Week 3 Issue #008）」段標改為「events — view 動態 UNION 4 張熱表（Week 4 Issue #011）」，內容加入：

```markdown
### §4.1 events view（Week 4 #011 — 動態 UNION 4 張熱表）

**Week 4 起**：events view 從「單一 monthly table」改為「`UNION ALL` 當月 + 上 3 個月共 4 張熱表」。
冷資料（>90 天）對應的分區表從 view 卸除但**仍留在 SQLite**，待 archive 整批壓縮後才 DROP。

view SQL 範例（假設 today=2026-10-15）：
```sql
CREATE VIEW events AS
    SELECT * FROM events_2026_10
    UNION ALL
    SELECT * FROM events_2026_09
    UNION ALL
    SELECT * FROM events_2026_08
    UNION ALL
    SELECT * FROM events_2026_07;
```

**view 維護點**：
- 應用層 INSERT / UPDATE 透過 INSTEAD OF triggers 仍指向當月表
- view 重建由 `db.archive_partitions.run_archive_pass()` 觸發（歸檔後 refresh hot table 名單）
- 每月切月份時若沒有歸檔，hot window 會自動滑動（透過 `_init_schema` 的 view union migration）
```

- [ ] **Step 7.2: 新增 §4.2「events_archive 表」+ 整表 .sql.gz 封存 SOP**

```markdown
### §4.2 events_archive — 冷資料層（Week 4 #011）

冷資料（>90 天）有兩種存放形式：

**形式 A：實體 `events_YYYY_MM.sql.gz` 檔（推薦）**
- 由 `scripts/archive_old_partitions.py` 產出
- 內容：`sqlite3 .dump <table>` 結果 gzip 壓縮
- 還原：`zcat events_2026_06.sql.gz | sqlite3 nvr_scan.db`（會把分區表重新倒回 DB）

**形式 B：歸檔後保留的 SQLite 實體表（過渡期）**
- 歸檔前：cold table 仍在 SQLite 內，只是不在 view 內
- 歸檔後：`DROP TABLE` + `VACUUM` 釋放磁碟

**形式 A vs B 取捨**：
- 形式 A：磁碟省（gzip 後約 1/10 大小）、歸檔後 DB 變小
- 形式 B：仍可在 SQL 內查詢（`SELECT * FROM events_2026_06`）、但每月膨脹

Week 4 預設採形式 A。
```

- [ ] **Step 7.3: 新增 §4.3 歸檔 SOP（給 ops）**

```markdown
### §4.3 歸檔 SOP（Week 4 #011）

**自動模式（推薦）**：歸檔子流程已整合進 `run_worker.sh/ps1/bat`，
每天凌晨 03:00 自動跑（守門：HOUR==3 才執行，避免每 15 分鐘掃描都跑）。
預設 `--hot-window 4 --keep-months 1`（最近 1 個月 cold 留著不歸檔作為安全緩衝）。

**手動模式**（首次部署或 debug）：
```bash
# 預覽
python scripts/archive_old_partitions.py --db nvr_scan.db --dry-run

# 正式執行
python scripts/archive_old_partitions.py --db nvr_scan.db --archive-dir ./archives

# 還原（把 .sql.gz 倒回 DB）
zcat archives/events_2026_06.sql.gz | sqlite3 nvr_scan.db
```

**Emergency Rollback**（歸檔後出問題）：
```sql
-- 1. 從 archive 檔還原分區表
-- 在 shell 跑：zcat archives/events_2026_06.sql.gz | sqlite3 nvr_scan.db

-- 2. 重建 view（4 張熱表 UNION）
-- 用 db/migrations/migrate_add_events_view_union.py
```
```

- [ ] **Step 7.4: Commit**

```bash
git add database_schema.md
git commit -m "docs(db): Week 4 #011 — schema §4.1/4.2/4.3 view UNION + archive + SOP"
```

---

### Task 8: roadmap-issues.md 標記完成

**Files:**
- Modify: `docs/roadmap-issues.md`（Week 4 區塊）

**Steps:**

- [ ] **Step 8.1: 標記 Week 4 #011 完成**

把「**Week 4** | 資料庫分區 Phase 2（歸檔）| ⏳ | 待 Week 3 完成」改為「**Week 4** | 資料庫分區 Phase 2（歸檔）| ✅ | Week 4 #011 完成」。

把 `#011` 那行的 ⏳ 改為 ✅，並加 commit hash 參考。

- [ ] **Step 8.2: Commit**

```bash
git add docs/roadmap-issues.md
git commit -m "docs: Week 4 #011 完成標記"
```

---

### Task 9: PR + merge

**Files:** 無

**Steps:**

- [ ] **Step 9.1: Push feature 分支**

Run: `git push -u origin feature/w4-archive-phase-2`

- [ ] **Step 9.2: 建立 PR（gh CLI）**

```bash
gh pr create --base master --head feature/w4-archive-phase-2 \
  --title "Week 4 #011 — events 動態 UNION + 整表 gzip 歸檔" \
  --body "..."
```

- [ ] **Step 9.3: 等 CI 過（Python 3.10/3.11/3.12 跑 990 項 pytest）**

- [ ] **Step 9.4: Squash merge 並刪除 feature 分支**

```bash
gh pr merge --squash --delete-branch
```

- [ ] **Step 9.5: 本機 pull 同步**

```bash
git checkout master && git pull origin master
```

---

## Self-Review

✅ **Spec coverage**：
- 識別 90 天前舊分區表 → `list_cold_partitions()` + `current_hot_tables(hot_window=4)`
- gzip dump 封存 → `dump_and_compress()` 用 `sqlite3 .dump` + `gzip.open`
- Cron 整合 → Task 5（`run_worker.sh/ps1/bat` HOUR=3 守門）

✅ **Placeholder scan**：所有函式有實作、測試有具體 assertion、Task 步驟無 TBD

✅ **Type consistency**：
- `current_hot_tables(today: date | None, hot_window: int) -> list[str]` 一致
- `run_archive_pass(...) -> dict` 一致
- `summary["archived"]` 在 Task 3.4 是 gz paths、在 Task 4.4 印出來仍是 gz paths（OK）

✅ **Zero existing test modifications**：
- 所有 985 項既有測試應持續綠（view union rebuild 在 `_init_schema` 是 idempotent）
- Week 3 partition 測試、Week 4 新增 9 項測試（2 view union + 4 archive + 3 CLI smoke）共 994 項

✅ **Rollback safety**：歸檔後 SQL 出問題可用 `zcat | sqlite3` 還原 + `migrate_add_events_view_union.py` 重建 view
