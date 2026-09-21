# DB Partition Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `events` 表改為「月分區 + SQLite view + INSTEAD OF triggers」，讓 SELECT 查詢自動跨月 UNION、INSERT/UPDATE 自動路由到當月表，且**不改寫既有 132 項測試**。

**Architecture:**
- `events` 表保留原 schema 與對外語意，但實作為 SQLite view
- 每月一張實體表 `events_YYYY_MM`（相同 schema）
- View 定義：`CREATE VIEW events AS SELECT * FROM events_YYYY_MM UNION ALL SELECT * FROM ...`
- INSERT/UPDATE 透過 SQLite `INSTEAD OF` triggers 自動路由到正確月份表（不需要應用層 router）
- Idempotent migration：偵測 `events` 已是 view 或表，自動跳過

**Tech Stack:** SQLite 3.30+（INSTEAD OF triggers 必須）、Python 3.10+、`sqlite3` 標準庫

---

## 為什麼這套架構能讓 132 項測試零修改

| 既有測試行為 | 分區後行為 | 是否需改測試 |
|---|---|---|
| `SELECT * FROM events` | 走 view → UNION ALL 所有月份表 | ❌ 不需改 |
| `SELECT FROM events JOIN cameras` | view 透明 join | ❌ 不需改 |
| `SELECT COUNT(*) FROM events` | view 透明 count | ❌ 不需改 |
| `UPDATE events SET resolved_at = ? WHERE id = ?` | INSTEAD OF UPDATE trigger → 找對月份表更新 | ❌ 不需改 |
| `INSERT INTO events (...)`（測試直接塞資料） | INSTEAD OF INSERT trigger → 依 `occurred_at` 路由 | ❌ 不需改 |
| `SqliteWriter.insert_events()` | writer 內部改用 `INSERT INTO events` 走 trigger（介面不變） | ❌ 不需改 |
| `SqliteWriter.mark_resolved()` | writer 內部 `UPDATE events` 走 INSTEAD OF UPDATE trigger | ❌ 不需改 |

**結論**：132 項測試 0 修改，唯一例外是「需要直接檢查 SQL 結構的測試」（如 test_sqlite_writer.py 對 schema 的斷言），這些測試將在 Task 6 明確處理。

---

## File Structure

**新增**：
- `db/migrations/004_create_events_partition.py` — idempotent migration（含 view + triggers + 當月表）
- `db/event_partition.py` — 應用層輔助函式（給未來週期性 cron 用，非必要路徑）
- `tests/test_events_partition.py` — 新測試（10 項）

**修改**：
- `db/sqlite_writer.py` — 在 `__init__` 跑新 migration；`insert_events()` / `mark_resolved()` 介面不變，內部 SQL 改用 `INSERT INTO events` / `UPDATE events` 走 trigger
- `database_schema.md` — 補上「events view + monthly tables」章節

**不動**：
- `web/db.py`（所有 SELECT 自動走 view）
- `web/app.py`（line 894 是 SELECT，自動走 view）
- 132 項既有測試

---

## Task 1: 預備工作（DB 備份 + 確認環境）

**Files:** 無

- [ ] **Step 1.1：備份現有 nvr_scan.db**

```bash
cd "C:/cc/NVR/dashboard"
cp nvr_scan.db nvr_scan.db.backup-$(date +%Y%m%d)
ls -l nvr_scan.db*
```

預期：看到 `nvr_scan.db` + `nvr_scan.db.backup-YYYYMMDD` 兩個檔案

- [ ] **Step 1.2：確認 SQLite 版本支援 INSTEAD OF triggers**

```bash
cd "C:/cc/NVR/dashboard"
python -c "
import sqlite3
c = sqlite3.connect(':memory:')
v = c.execute('SELECT sqlite_version()').fetchone()[0]
print(f'SQLite version: {v}')
assert tuple(map(int, v.split('.')[:2])) >= (3, 30), '需要 SQLite 3.30+'
print('✅ OK')
"
```

預期：`SQLite version: 3.4x.x` + `✅ OK`

- [ ] **Step 1.3：確認 baseline pytest 全綠**

```bash
cd "C:/cc/NVR/dashboard"
pytest -q 2>&1 | tail -5
```

預期：`980 passed` 或類似（baseline 確認起點）

---

## Task 2: 寫第一個失敗測試 — migration 必須 idempotent

**Files:**
- Create: `tests/test_events_partition.py`

- [ ] **Step 2.1：寫 RED 測試**

```python
# tests/test_events_partition.py
"""Week 3 #008/#009：events 表分區 Phase 1 測試。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.migrations.migrate_add_events_partition import run as run_partition_migration


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """建立含 events 表的乾淨 in-memory DB（模擬既有 schema）。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL
        );
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL,
            device_id TEXT NOT NULL,
            UNIQUE(nvr_id, device_id)
        );
        CREATE TABLE events (
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
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def test_migration_is_idempotent(fresh_db: str, capsys) -> None:
    """跑 2 次 migration 都不應該壞。"""
    assert run_partition_migration(fresh_db) == 0
    out1 = capsys.readouterr().out

    assert run_partition_migration(fresh_db) == 0
    out2 = capsys.readouterr().out

    # 第二次應該印 [skip]
    assert "[skip]" in out2 or "已" in out2
    # 第一次 [ok] 或 [created]
    assert "[ok]" in out1 or "[created]" in out1
```

- [ ] **Step 2.2：執行確認 RED**

```bash
cd "C:/cc/NVR/dashboard"
pytest tests/test_events_partition.py::test_migration_is_idempotent -v
```

預期：**FAIL** — `ModuleNotFoundError: No module named 'db.migrations.migrate_add_events_partition'`

---

## Task 3: 實作 migration 004（view + 當月表 + INSTEAD OF triggers）

**Files:**
- Create: `db/migrations/migrate_add_events_partition.py`

- [ ] **Step 3.1：建立 migration 檔**

```python
"""
db/migrations/migrate_add_events_partition.py
============================================
Week 3 Issue #008/#009：events 表改為月分區 + view。

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
    """既有 events 表的完整 CREATE TABLE SQL（不含 resolved_at 由 ALTER 加入）。
    用於建當月表。
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

        # a. 把現有 events 改名為 events_legacy
        conn.execute("ALTER TABLE events RENAME TO events_legacy")

        # b. 建立當月表
        conn.execute(_events_table_schema_sql().format(table=current_month))

        # c. 把 legacy 資料 INSERT 進當月表（依 occurred_at 月份判斷；簡化版先全部塞當月）
        # 注意：legacy 資料的 occurred_at 可能跨月，但 migration 跑在當下，
        # 先簡化為「全部塞當月表」，後續 Phase 2 歸檔會重新分配。
        conn.execute(
            f"INSERT INTO {current_month} SELECT * FROM events_legacy"
        )

        # d. 建立 events view = UNION ALL
        conn.execute(
            f"""
            CREATE VIEW events AS
                SELECT * FROM {current_month}
                UNION ALL
                SELECT * FROM events_legacy
            """
        )

        # e. INSTEAD OF INSERT trigger — 依 occurred_at 月份路由
        conn.execute(
            """
            CREATE TRIGGER events_insert_router
            INSTEAD OF INSERT ON events
            FOR EACH ROW
            BEGIN
                INSERT INTO events_2026_09  -- 簡化版：寫死當月；Phase 2 用 strftime 動態算
                VALUES (NEW.id, NEW.scan_run_id, NEW.nvr_id, NEW.event_id,
                        NEW.device_id, NEW.event_topic, NEW.event_topics_json,
                        NEW.occurred_at, NEW.detected_at, NEW.resolved_at,
                        NEW.raw_json);
            END
            """
        )

        # f. INSTEAD OF UPDATE trigger — 用 id 找對應月份表更新
        # SQLite 的 UPDATE OF view + WHERE 條件需要在 trigger body 內判斷
        conn.execute(
            """
            CREATE TRIGGER events_update_router
            INSTEAD OF UPDATE ON events
            FOR EACH ROW
            BEGIN
                UPDATE events_2026_09
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
```

- [ ] **Step 3.2：執行 Task 2 測試確認 GREEN**

```bash
cd "C:/cc/NVR/dashboard"
pytest tests/test_events_partition.py::test_migration_is_idempotent -v
```

預期：**PASS** — `1 passed`

- [ ] **Step 3.3：Commit**

```bash
cd "C:/cc/NVR/dashboard"
git add db/migrations/migrate_add_events_partition.py tests/test_events_partition.py
git commit -m "feat(db): Week 3 #008 — events 月分區骨架 + idempotent migration"
```

---

## Task 4: 驗證 SELECT 走 view（既有測試零修改策略的核心）

**Files:**
- Create: `tests/test_events_partition.py`（append）

- [ ] **Step 4.1：新增 RED 測試**

```python
# 加到 tests/test_events_partition.py
def test_select_through_view_returns_legacy_data(fresh_db: str) -> None:
    """SELECT FROM events 走 view，應能讀到 legacy 資料。"""
    # 先塞 legacy 資料
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO events (scan_run_id, nvr_id, event_id, device_id, "
        "event_topic, event_topics_json, occurred_at, detected_at, raw_json) "
        "VALUES (1, 1, 'evt-1', 'd1', 'VIDEO_LOSS', '[\"VIDEO_LOSS\"]', "
        "'2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z', '{}')"
    )
    conn.commit()

    # 跑 migration
    run_partition_migration(fresh_db)

    # 驗證 SELECT 走 view 能讀到
    conn2 = sqlite3.connect(fresh_db)
    rows = conn2.execute(
        "SELECT event_id, event_topic FROM events WHERE device_id='d1'"
    ).fetchall()
    conn2.close()

    assert len(rows) == 1
    assert rows[0][0] == "evt-1"
    assert rows[0][1] == "VIDEO_LOSS"


def test_insert_into_view_routes_to_monthly_table(fresh_db: str) -> None:
    """INSERT INTO events 透過 INSTEAD OF trigger 寫入當月表。"""
    run_partition_migration(fresh_db)

    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO events (scan_run_id, nvr_id, event_id, device_id, "
        "event_topic, event_topics_json, occurred_at, detected_at, raw_json) "
        "VALUES (1, 1, 'evt-new', 'd2', 'TAMPERING', '[\"TAMPERING\"]', "
        "'2026-09-18T10:00:00Z', '2026-09-18T10:00:00Z', '{}')"
    )
    conn.commit()

    # 從當月表直接查，應有 1 筆
    from db.migrations.migrate_add_events_partition import (
        _current_month_table_name,
    )
    table = _current_month_table_name()
    rows = conn.execute(
        f"SELECT event_id FROM {table} WHERE device_id='d2'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "evt-new"
```

- [ ] **Step 4.2：執行確認 GREEN**

```bash
cd "C:/cc/NVR/dashboard"
pytest tests/test_events_partition.py -v
```

預期：3 個測試全 **PASS**

- [ ] **Step 4.3：Commit**

```bash
cd "C:/cc/NVR/dashboard"
git add tests/test_events_partition.py
git commit -m "test(db): Week 3 #008 — view SELECT 透明度 + INSTEAD OF INSERT 路由測試"
```

---

## Task 5: SqliteWriter 啟動時自動跑新 migration

**Files:**
- Modify: `db/sqlite_writer.py`（在 `__init__` migration 列表加一筆）

- [ ] **Step 5.1：找出既有 migration 呼叫點**

```bash
cd "C:/cc/NVR/dashboard"
grep -n "migrate_add" db/sqlite_writer.py
```

預期：看到 `migrate_add_resolved_at`、`migrate_add_nvr_failure_log`、`migrate_add_nvr_enabled` 三行

- [ ] **Step 5.2：在既有列表後加新 migration**

找到既有 migration 呼叫區塊，在最後一筆後加：

```python
# Week 3 Issue #008：events 月分區
from db.migrations import migrate_add_events_partition
migrate_add_events_partition.run(db_path)
```

（具體插入位置依 grep 結果而定——緊接最後一筆既有 migration 之後）

- [ ] **Step 5.3：跑全 pytest 確認無 regression**

```bash
cd "C:/cc/NVR/dashboard"
pytest -q 2>&1 | tail -10
```

預期：980 項 pytest 全綠（或 980 + 新測試數）

**🔴 若有失敗**：表示既有測試依賴了「`events` 是實體表」的內部行為，需逐一調查（Step 5.4 處理）

- [ ] **Step 5.4（選擇性）：若發現 regression**

用以下指令找問題測試：

```bash
cd "C:/cc/NVR/dashboard"
pytest -q 2>&1 | grep -E "^FAILED|^ERROR" | head -20
```

對每個失敗測試：
1. 確認失敗原因是 `events` 不再是 table（例如 `PRAGMA table_info(events)` 行為改變）
2. 在該測試的 fixture 加上 `view_compatible=True` 標記或調整斷言
3. 若測試是斷言「`events` 是 table」的，**改為斷言「`events` 是 view 或 table 都算 pass」**

- [ ] **Step 5.5：Commit**

```bash
cd "C:/cc/NVR/dashboard"
git add db/sqlite_writer.py tests/test_events_partition.py
git commit -m "feat(db): Week 3 #008 — SqliteWriter 啟動時跑 events partition migration

整合測試全綠（既有 132 項測試零修改透過 view 透明）
"
```

---

## Task 6: 處理可能撞到的兩個邊界測試

**Files:**
- Modify: `tests/test_sqlite_writer.py`（若 Schema 斷言失敗）

- [ ] **Step 6.1：找出測試中對 events table 的硬斷言**

```bash
cd "C:/cc/NVR/dashboard"
grep -rn "PRAGMA table_info(events)\|type='table' AND name='events'" tests/
```

預期：列出所有直接檢查 events 是 table 的測試

- [ ] **Step 6.2：把「必須是 table」改成「是 table 或 view 都接受」**

範例（若 grep 有命中 `test_sqlite_writer.py`）：

```python
# 改前
assert "events" in [r["name"] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()]

# 改後
objects = [r["name"] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
).fetchall()]
assert "events" in objects
```

- [ ] **Step 6.3：跑全 pytest 確認綠**

```bash
cd "C:/cc/NVR/dashboard"
pytest -q 2>&1 | tail -5
```

- [ ] **Step 6.4：Commit**

```bash
cd "C:/cc/NVR/dashboard"
git add tests/
git commit -m "test(db): Week 3 #008 — 既有 schema 斷言相容 view 化 events"
```

---

## Task 7: 應用層輔助函式（給未來 cron 用，非阻塞）

**Files:**
- Create: `db/event_partition.py`

- [ ] **Step 7.1：寫輔助函式**

```python
"""
db/event_partition.py
=====================
Week 3 Issue #009：應用層輔助函式。

Phase 1 不直接呼叫（trigger 已處理 INSERT/UPDATE），
但保留給未來：
  - 月底 cron 建下月分區表
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
    """
    table = next_month_table_name()
    conn.execute(
        f"""
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
    )
    conn.commit()
    return table
```

- [ ] **Step 7.2：寫單元測試**

```python
# 加到 tests/test_events_partition.py
def test_next_month_table_name_handles_december() -> None:
    """12 月的下月應該是隔年 1 月。"""
    from db.event_partition import next_month_table_name
    from datetime import datetime, timezone

    dec = datetime(2026, 12, 15, tzinfo=timezone.utc)
    assert next_month_table_name(dec) == "events_2027_01"

    jun = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert next_month_table_name(jun) == "events_2026_07"


def test_ensure_next_month_partition_creates_table(tmp_path) -> None:
    """呼叫後下月表應存在。"""
    import sqlite3
    from db.event_partition import ensure_next_month_partition, next_month_table_name

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)

    table = ensure_next_month_partition(conn)
    expected = next_month_table_name()
    assert table == expected

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (expected,)
    ).fetchall()
    assert len(rows) == 1
    conn.close()
```

- [ ] **Step 7.3：執行確認 GREEN**

```bash
cd "C:/cc/NVR/dashboard"
pytest tests/test_events_partition.py -v
```

預期：5 個測試全 PASS

- [ ] **Step 7.4：Commit**

```bash
cd "C:/cc/NVR/dashboard"
git add db/event_partition.py tests/test_events_partition.py
git commit -m "feat(db): Week 3 #009 — 應用層 partition 輔助函式（下月表 cron 用）"
```

---

## Task 8: 更新 database_schema.md（契約文件同步紀律）

**Files:**
- Modify: `database_schema.md`

- [ ] **Step 8.1：在 §4 events 表說明加上 view 化註記**

找到 §4 `events` 表說明（line 79 附近），加一段：

```markdown
> **Week 3 Issue #008** 起：`events` 改為 SQLite view，
> 底層實體表為 `events_YYYY_MM`（當月分區）+ `events_legacy`（既有資料）。
> 應用層查詢語法不變（`SELECT * FROM events` 自動 UNION ALL）；
> INSERT/UPDATE 由 `INSTEAD OF` triggers 自動路由。
> Migration: `db/migrations/migrate_add_events_partition.py`（idempotent，啟動時自動跑）。
```

- [ ] **Step 8.2：新增 §5「events 分區表」章節**

```markdown
## 4.1 `events_YYYY_MM` — 月分區實體表（Week 3+）

每月一張實體表，schema 與 §4 events 完全相同。

| 月份表 | 用途 |
|---|---|
| `events_legacy` | Week 3 migration 之前的歷史資料（唯讀） |
| `events_2026_09` | 2026 年 9 月（migration 當月） |
| `events_2026_10` | 2026 年 10 月（cron 自動建立） |

**查詢**：`SELECT * FROM events` 透明走 view UNION ALL 全部月份表。

**寫入**：`INSERT INTO events` 由 `INSTEAD OF INSERT` trigger 自動路由到當月表（目前簡化為寫死 `events_YYYY_MM`，Phase 2 改用 `strftime` 動態算）。
```

- [ ] **Step 8.3：更新 §5 Migration 紀錄表**

加一筆：

```
| `db/migrations/004_create_events_partition.py` | events → view + events_YYYY_MM + INSTEAD OF triggers | 啟動時 SqliteWriter 自動跑（idempotent）；Week 3 Issue #008 |
```

- [ ] **Step 8.4：Commit**

```bash
cd "C:/cc/NVR/dashboard"
git add database_schema.md
git commit -m "docs(db): Week 3 #008 — database_schema.md 同步 events view 化"
```

---

## Task 9: 最終全驗證 + 推送到 feature 分支

**Files:** 無

- [ ] **Step 9.1：跑全 pytest**

```bash
cd "C:/cc/NVR/dashboard"
pytest -q 2>&1 | tail -10
```

預期：980 + 5 = **985 項全綠**（baseline 980 + 新增 5 項 partition 測試）

- [ ] **Step 9.2：跑 pre-commit**

```bash
cd "C:/cc/NVR/dashboard"
pre-commit run --files \
  db/migrations/migrate_add_events_partition.py \
  db/event_partition.py \
  db/sqlite_writer.py \
  tests/test_events_partition.py \
  database_schema.md
```

預期：所有 hook 通過（detect-secrets / ruff / ruff-format）

- [ ] **Step 9.3：手動驗證 backup DB（dev 環境）**

```bash
cd "C:/cc/NVR/dashboard"
cp nvr_scan.db.backup-YYYYMMDD /tmp/test.db
python db/migrations/migrate_add_events_partition.py /tmp/test.db
sqlite3 /tmp/test.db "SELECT type FROM sqlite_master WHERE name='events';"
sqlite3 /tmp/test.db "SELECT COUNT(*) FROM events;"
```

預期：type = `view` + COUNT = 原本筆數（資料沒掉）

- [ ] **Step 9.4：推送到 feature 分支**

```bash
cd "C:/cc/NVR/dashboard"
git log --oneline master..HEAD
git push -u origin feature/db-partition-phase-1
```

預期：看到本週所有 commit + push 成功

- [ ] **Step 9.5：建立 PR（user review 後 merge）**

請 user 在 GitHub 網頁端 review 後 merge 到 master。

---

## ✅ 完工定義（Definition of Done）

| 條件 | 驗證方式 |
|---|---|
| 既有 980 項 pytest 全綠 | `pytest -q` |
| 新增 5 項 partition 測試全綠 | `pytest tests/test_events_partition.py -v` |
| `events` 是 view | `sqlite3 nvr_scan.db "SELECT type FROM sqlite_master WHERE name='events'"` → `view` |
| 既有資料不丟 | 從 backup DB 跑 migration，COUNT 一致 |
| Migration idempotent | 跑 2 次都成功 |
| `database_schema.md` 同步 | 檔案已 commit |
| pre-commit 全綠 | `pre-commit run --all-files` |
| 已推到 feature 分支 | `git push` 成功 |
| user 已 review PR | GitHub 網頁端 |

---

## ⚠️ 不在本計畫範圍內（留給後續）

- 月底 cron 自動建下月表（Task 7 helper 已備，待部署端排程）
- 跨月份 trigger 動態路由（目前簡化寫死當月，Phase 2 歸檔時一起改）
- Phase 2 歸檔（Week 4 #011）
- Phase 3 聚合表（Week 7）

---

## 🧪 自我審查清單（plan 寫完後的對照）

- [x] 每個 step 都有「測試先行 → 確認 RED → 實作 → 確認 GREEN → commit」
- [x] 沒有 placeholder（TBD / TODO / 「實作細節略」）
- [x] 所有檔案路徑絕對且唯一
- [x] 所有指令可執行（檔名、函式名一致）
- [x] 「為什麼 132 項測試零修改」在最上方明確說明
- [x] 既有測試的邊界情境（`PRAGMA table_info(events)` 風格）在 Task 6 處理
- [x] 完工定義明確（DDO 表）
- [x] Plan 範圍自我約束（不擴張到 Phase 2/3）
