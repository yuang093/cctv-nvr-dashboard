# 資料結構定義 (Database Schema / Data Models)

## 設計原則
- **當前**：SQLite 為主，檔案式資料庫，無外部依賴。
- **未來**：保留介面抽象層，未來可切換至 PostgreSQL / MySQL，schema 命名與型別需相容。
- **時間欄位**：一律使用 `TEXT` 儲存 ISO 8601（UTC），避免時區混淆。
- **ID 策略**：對外 ID（`nvr_id`、`device_id`、`event_id`）使用 NVR 原始值；對內主鍵一律為 `INTEGER PRIMARY KEY AUTOINCREMENT`。

## 資料表清單
1. `nvr_servers` — NVR 設定檔內容鏡像（執行時載入）
2. `cameras` — 每台 NVR 下的攝影機清單
3. `scan_runs` — 每次掃描執行的記錄
4. `events` — 當次掃描偵測到的異常事件（ACTIVE）
5. `image_health_checks` — Phase 2.8（Arisan 影像健康巡檢）每張 cam 縮圖分析紀錄
6. `discover_sessions` — Phase 2.8（Arisan 探索網段）每次探索任務紀錄
7. `event_kind_catalog` — Phase 2.8（Arisan）17 種事件主題中文顯示字典

---

## 1. `nvr_servers` — NVR 伺服器設定
鏡像 `nvr_config.json`，方便 SQL 直接查詢每台 NVR 狀態。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | 內部主鍵 |
| `nvr_id` | TEXT UNIQUE NOT NULL | 設定檔中的 `id`（例：`branch-a`） |
| `name` | TEXT NOT NULL | 人類可讀名稱 |
| `host` | TEXT NOT NULL | IP 或 hostname |
| `port` | INTEGER NOT NULL DEFAULT 8443 | HTTPS port |
| `username` | TEXT | 帳號（⚠️ 生產環境應加密或由 secrets manager 注入） |
| `password` | TEXT | 密碼（同上警告） |
| `verify_ssl` | INTEGER NOT NULL DEFAULT 0 | 0/1，是否驗證 SSL（自簽證書預設 0） |
| `site_id` | TEXT NULL | 未來多站台擴充 |
| `tags` | TEXT NULL | JSON 陣列字串，標籤分類 |
| `enabled` | INTEGER NOT NULL DEFAULT 1 | **v2.7+ 起**：啟用狀態（1=啟用、0=停用）；取代 nvr_config.json 的 enabled 過濾 |
| `created_at` | TEXT NOT NULL | 建立時間（UTC ISO 8601） |
| `updated_at` | TEXT NOT NULL | 更新時間 |

**v2.7+ 變更說明**：
- `enabled` 欄位由 DB 管理，背景掃描（CLI + Web UI）改從 `web.db.list_enabled_nvrs()` 讀
- `nvr_config.json` 降級為「首次 init 種子」：CLI 偵測 DB 為空時自動從 nvr_config.json seed
- 對 v2.6 既有 DB：`SqliteWriter.__init__()` 啟動時自動 `ALTER TABLE ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1`（idempotent；舊資料預設全啟用）
- UI 控制：`POST /nvrs/<internal_id>/toggle` 切換啟用狀態（保留資料不刪除）

---

## 2. `cameras` — 攝影機主檔
跨掃描週期持續累積（upsert），用於名稱解析與歷史關聯。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | 內部主鍵 |
| `nvr_id` | INTEGER NOT NULL FK→`nvr_servers.id` | 所屬 NVR |
| `device_id` | TEXT NOT NULL | NVR 原始 deviceId（同一 NVR 內唯一） |
| `camera_name` | TEXT NOT NULL | NVR 上的攝影機名稱 |
| `last_seen_at` | TEXT NOT NULL | 最近一次掃描有看到此設備的時間 |
| `UNIQUE` | (nvr_id, device_id) | 防止重複 |

---

## 3. `scan_runs` — 掃描執行紀錄
每次批次掃描一筆，用來追蹤「什麼時候、掃了誰、耗時多久、撈到幾個事件」。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | 內部主鍵 |
| `started_at` | TEXT NOT NULL | 開始時間（UTC） |
| `finished_at` | TEXT NULL | 結束時間（NULL 表示仍在跑或失敗） |
| `status` | TEXT NOT NULL | `running` / `success` / `partial` / `failed` |
| `total_nvrs` | INTEGER NOT NULL | 本次預計掃描 NVR 數 |
| `ok_nvrs` | INTEGER NOT NULL DEFAULT 0 | 成功完成數 |
| `failed_nvrs` | INTEGER NOT NULL DEFAULT 0 | 失敗數 |
| `total_cameras` | INTEGER NOT NULL DEFAULT 0 | 總設備數 |
| `abnormal_cameras` | INTEGER NOT NULL DEFAULT 0 | 異常設備數 |
| `error_message` | TEXT NULL | 失敗時的錯誤訊息 |

---

## 4. `events` — 異常事件
每筆 ACTIVE 事件一筆；用於歷史分析與報表查詢。Phase 1 起支援 resolved 追蹤：掃描時若事件對應的相機重新 CONNECTED，會把 `resolved_at` 寫入。

> **Week 3 Issue #008 起**：`events` 改為 SQLite view，底層實體表為 `events_YYYY_MM`（當月分區）+ `events_legacy`（既有資料）。應用層查詢語法不變（`SELECT * FROM events` 自動看當月表）；INSERT/UPDATE 由 `INSTEAD OF` triggers 自動路由到當月表。詳見 §4.1。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | 內部主鍵 |
| `scan_run_id` | INTEGER NOT NULL FK→`scan_runs.id` | 所屬掃描執行 |
| `nvr_id` | INTEGER NOT NULL FK→`nvr_servers.id` | 所屬 NVR（冗餘以加速查詢） |
| `camera_id` | INTEGER NULL FK→`cameras.id` | 對應攝影機（可能 N/A） |
| `event_id` | TEXT NOT NULL | NVR 原始 eventId |
| `device_id` | TEXT NOT NULL | NVR 原始 deviceId |
| `event_topic` | TEXT NOT NULL | 主要事件主題（例：`VIDEO_LOSS`） |
| `event_topics_json` | TEXT NOT NULL | 完整 eventTopics 陣列 JSON 字串 |
| `occurred_at` | TEXT NOT NULL | 事件時間（UTC ISO 8601） |
| `detected_at` | TEXT NOT NULL DEFAULT (datetime('now')) | 本系統偵測時間 |
| `resolved_at` | TEXT NULL | 事件解除時間（UTC ISO 8601；NULL=進行中，**Phase 1 新增**） |
| `raw_json` | TEXT NOT NULL | 原始事件 JSON（供日後除錯） |
| `INDEX` | (scan_run_id), (nvr_id, occurred_at), (detected_at) | 加速查詢；Week 3 起綁定在當月 monthly table |

**`resolved_at` 規則**：
- `NULL` → 事件進行中（紅色標記）
- 非 `NULL` → 已恢復（綠色標記；值為 UTC ISO 8601 字串）
- 由 worker 在每次 `finish_scan_run()` 之後依相機連線狀態自動更新
- 對 v1 既有 DB：`SqliteWriter.__init__()` 啟動時自動 `ALTER TABLE`（idempotent）

---

## 4.1 `events` view — 動態 UNION 4 張熱表（Week 4 Issue #011）

**Week 4 起**：`events` view 從「單一 monthly table」改為「`UNION ALL` 當月 + 上 3 個月共 4 張熱表」。
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

| 物件 | 角色 |
|---|---|
| `events` (view) | 4 張熱表 UNION；應用層 `SELECT * FROM events` 完全透明 |
| `events_insert_router` (INSTEAD OF INSERT) | 寫入當月 `events_YYYY_MM` |
| `events_update_router` (INSTEAD OF UPDATE) | 依 `OLD.id` 更新當月表 |
| `events_YYYY_MM` 實體表 | 4 張熱表（hot_window=4，含當月） |
| `events_legacy` | Week 3 migration 前的歷史資料（唯讀，emergency rollback） |

**寫入**：
- `INSERT INTO events` → INSTEAD OF INSERT trigger 路由到**當月** `events_YYYY_MM`（hot[0]）
- `UPDATE events SET ...` → INSTEAD OF UPDATE trigger 依 `OLD.id` 找對應月份表更新
- 已知 SQLite 限制：`cur.rowcount` 在 INSTEAD OF UPDATE 下永遠回傳 0；`mark_resolved()` 改用預先 `SELECT COUNT` 取得實際匹配數

**索引**（綁定在 monthly table，SQLite 不支援 view 上的 index）：
- `uq_events_open_per_topic`（partial UNIQUE：`(nvr_id, device_id, event_topic) WHERE resolved_at IS NULL`）
- `idx_events_scan_run_id` (`scan_run_id`)
- `idx_events_nvr_occurred` (`nvr_id, occurred_at`)
- `idx_events_detected_at` (`detected_at`)

**Migration**：
- `db/migrations/migrate_add_events_partition.py` — Week 3：events → view + 當月表 + INSTEAD OF triggers
- `db/migrations/migrate_add_events_view_union.py` — Week 4：view 改為 UNION 4 張熱表（共用 `db.event_partition.rebuild_events_view` helper）

**view 維護點**：
- view + triggers 重建由 `db.event_partition.rebuild_events_view()` 統一處理
- 觸發時機：(a) SqliteWriter `_init_schema` 啟動時自動 rebuild；(b) 歸檔後 `run_archive_pass()` 內 rebuild

**Emergency Rollback**（萬一 view/trigger 出問題）：
```sql
DROP VIEW events;
ALTER TABLE events_legacy RENAME TO events;
DROP TABLE events_YYYY_MM;
```

## 4.2 `events` 冷資料歸檔 SOP（Week 4 Issue #011）

**目標**：當某個舊月份實體表的所有資料都確定超過 90 天（超出 hot window）時，
將整張表 dump 成 `.sql.gz` 封存檔並 DROP，釋放磁碟空間。

**歸檔流程**（`scripts/archive_old_partitions.py`）：
```
1. list_cold_partitions() → 識別 hot window 外的 events_YYYY_MM 表
2. keep_months=1 安全緩衝：保留最新 N 個月 cold 不歸檔
3. dump_and_compress() → Python sqlite3.iterdump() + gzip → events_YYYY_MM.sql.gz
4. drop_and_vacuum() → DROP TABLE + VACUUM（釋放 .db 檔案實際磁碟空間）
5. rebuild_events_view() → 重建 view 與 INSTEAD OF triggers
```

**產物格式**：`./archives/events_YYYY_MM.sql.gz`
- 內容：`CREATE TABLE events_YYYY_MM ...` + `INSERT INTO events_YYYY_MM ...`
- 還原：`zcat archives/events_YYYY_MM.sql.gz | sqlite3 nvr_scan.db`（會把分區表重新倒回 DB）

**排程整合**：`run_worker.sh` / `.ps1` / `.bat` 在主掃描前守門，**每週日凌晨 03:00** 自動執行：
- `HOUR==03 && DOW==7`（bash）/ `Hour=3 And DayOfWeek=0`（PowerShell）/ `%HOUR%== 3 if %WEEKDAY%==0`（cmd）
- 預設參數：`--hot-window 4 --keep-months 1`（總緩衝 120 天，給跨季度調閱用）

**Manual 操作**：
```bash
# 預覽（不實際執行）
python scripts/archive_old_partitions.py --db nvr_scan.db --dry-run

# 正式執行
python scripts/archive_old_partitions.py --db nvr_scan.db --archive-dir ./archives

# 還原封存檔
zcat archives/events_2026_06.sql.gz | sqlite3 nvr_scan.db
```

**安全邊界**：
- `events_legacy` 不符合 `events_2%` pattern，**永遠不會被誤刪**
- hot tables 即使存在 0 筆資料也會被 UNION（SQLite view 行為一致）
- keep_months 預設 1 個月 = 跨季度調閱緩衝


---

## 5. `nvr_failure_log` — 個別 NVR 連線失敗紀錄（v2.7+）

**目的**：當 batch_scan 中某台 NVR 連線失敗（timeout / auth 錯 / 拒連 / upsert 失敗…）時，
除了更新 `scan_runs.failed_nvrs` 計數外，**把個別 NVR 的失敗原因**寫進這張表，
給 dashboard / run_detail 顯示具體哪台 NVR 出了什麼事。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `scan_run_id` | INTEGER FK→scan_runs | 這次掃描的 run |
| `nvr_id` | TEXT | NVR 設定檔的 id（例 "NVR-A"） |
| `nvr_name` | TEXT | 顯示名稱 snapshot（nvr_config 改了不影響歷史） |
| `nvr_internal_id` | INTEGER (nullable) | nvr_servers.id；upsert 失敗時為 NULL |
| `error_type` | TEXT | 例 "ConnectionError" / "Timeout" / "AuthError"（=exc 類別名） |
| `error_message` | TEXT | 完整錯誤訊息 |
| `failed_at` | TEXT | ISO 8601 UTC |

**索引**：
- `idx_nvr_failure_log_scan_run_id`：給 run_detail 查這次 run 的所有失敗
- `idx_nvr_failure_log_nvr_id_failed_at`：給 NVR 歷史故障趨勢查詢

**寫入時機**：
- `batch_scan.py` 的 per-NVR `except Exception` 內呼叫 `writer.log_nvr_failure(...)`
- `upsert_nvr` 失敗（極少見）也會寫一筆（`nvr_internal_id` = NULL）
- 不主動 commit；由 `finish_scan_run()` 的 transaction commit 一起持久化

**與 `scan_runs.failed_nvrs` 的關係**：
- `scan_runs.failed_nvrs`：計數（O(1) 寫入）
- `nvr_failure_log`：明細（給人看「哪台、為何」）
- 兩者永遠一致（同一 transaction 內同步寫入）

**對 v2.6 既有 DB**：`SqliteWriter.__init__()` 啟動時自動 CREATE TABLE IF NOT EXISTS（idempotent）

---

## 記憶體資料結構（Worker 內短期使用）
`AvigilonScanner` 在記憶體中維護以下結構，於 `save_to_db()` 呼叫時序列化入庫：

```python
# 單次掃描一台 NVR 的輸出（dict）
{
    "nvr_id": "branch-a",
    "scan_run_id": 42,
    "cameras": {
        "deviceId-1": "大門入口",
        "deviceId-2": "停車場",
        ...
    },
    "events": [
        {
            "event_id": "...",
            "device_id": "deviceId-2",
            "event_topic": "VIDEO_LOSS",
            "event_topics": ["VIDEO_LOSS"],
            "occurred_at": "2026-06-18T03:21:00Z",
            "raw_json": {...}
        }
    ],
    "stats": {
        "total_cameras": 32,
        "abnormal_cameras": 1
    }
}
```

## 擴充介面預留（v2+）
- `events` 表 `resolved_at` 欄位：**Phase 1 已實作**（2026-06-30）
- 可新增 `webhook_subscriptions` 表儲存推播設定。
- 可新增 `users` / `roles` 表支援 Web UI 權限管理（屆時再設計）。
- **`clips_log` 表**（Phase 2.7 規劃中，目前 **未實作**）：給調閱者下載時留 audit log。
  預期欄位（待 user 決定後落地）：
  ```sql
  CREATE TABLE IF NOT EXISTS clips_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
      camera_id TEXT NOT NULL,
      requested_at TEXT NOT NULL,             -- 查詢時間 (UTC ISO 8601)
      clip_start TEXT NOT NULL,               -- 片段開始 (UTC ISO 8601)
      clip_end TEXT NOT NULL,                 -- 片段結束 (UTC ISO 8601)
      requester TEXT,                         -- 從哪裡調閱 (UI user / API key)
      download_path TEXT                      -- 若有落地，存哪
  );
  ```
  v2.7 預設 **不寫** clips_log（純 stream 起步）；若需要 audit，後續 migration 補上。

---

## 5. Migration 紀錄

| 版本 | 描述 | 套用時機 |
|---|---|---|
| `db/migrations/001_add_resolved_at.sql` | events 表加 `resolved_at TEXT NULL` | 啟動時 SqliteWriter 自動跑；舊 DB 也適用（idempotent） |
| `db/migrations/002_add_nvr_failure_log.sql` | 新建 `nvr_failure_log` 表 + 2 個索引 | 啟動時 SqliteWriter 自動跑（idempotent）；記錄個別 NVR 連線失敗原因 |
| `db/migrations/003_add_nvr_enabled.sql` | nvr_servers 表加 `enabled INTEGER NOT NULL DEFAULT 1` | 啟動時 SqliteWriter 自動跑；舊 DB 預設全啟用（idempotent）；v2.7+ 起由 DB 管理啟用狀態 |
| `db/migrations/004_create_events_partition.py` | events → view + events_YYYY_MM + events_legacy + INSTEAD OF triggers + 4 個索引 | 啟動時 SqliteWriter 自動跑（idempotent）；Week 3 Issue #008。Schema 變更：見 §4.1 |
| `db/migrations/005_create_events_view_union.py` | events view 改為動態 UNION 4 張熱表 + 重建 INSTEAD OF triggers | 啟動時 SqliteWriter 自動跑（idempotent）；Week 4 Issue #011。Schema 變更：見 §4.1 |
| Phase 2.8（inline in `db/sqlite_writer.py`） | 加 `image_health_checks` / `discover_sessions` / `event_kind_catalog` 3 表 + `cameras.last_health_check_id` 欄位 + 17 筆 event_kind_catalog seed | 啟動時 SqliteWriter 自動跑（idempotent） |

---

## 6. `image_health_checks` — 影像健康巡檢紀錄（Phase 2.8）

每張 cam 縮圖分析結果。worker 對 active cam 抓 2 張 jpeg（間隔 5s）後分析：
- 模糊（blur_var）
- 過曝/欠曝（mean_luma）
- 凍結（兩張影像 mean abs pixel diff）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | 內部主鍵 |
| `camera_id` | TEXT NOT NULL | Avigilon camera device_id |
| `nvr_server_id` | INTEGER NULL | FK → `nvr_servers.id`（建議加 INDEX） |
| `checked_at_utc` | TEXT NOT NULL | ISO 8601 UTC |
| `metrics_json` | TEXT NOT NULL | JSON：`{"blur_var": ..., "mean_luma": ..., "frozen_diff": ...}` |
| `flags_json` | TEXT NOT NULL | JSON array：`["blurry", "under"]` |
| `triggered_event_ids` | TEXT NULL | JSON array of event id（若新觸發則寫 events 表並回填） |

索引：
- `idx_health_cam_time(camera_id, checked_at_utc DESC)` — 查單台 cam 最新檢查
- `idx_health_nvr(nvr_server_id)` — 跨 NVR 統計

---

## 7. `discover_sessions` — 探索網段任務紀錄（Phase 2.8）

每次 `POST /devices/discover` 啟動的探索任務（含 CIDR、結果、狀態）。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | INTEGER PK | 內部主鍵 |
| `started_at_utc` | TEXT NOT NULL | ISO 8601 UTC |
| `finished_at_utc` | TEXT NULL | 探索完成時間 |
| `cidr` | TEXT NOT NULL | 例 `"192.168.0.0/24"` |
| `results_json` | TEXT NOT NULL | JSON array of `{ip, open, nvr?, added_to_db?}` |
| `status` | TEXT NOT NULL | `'running'` / `'completed'` / `'failed'` |

---

## 8. `event_kind_catalog` — 17 種事件主題中文字典（Phase 2.8）

UI 顯示單一真相：所有 events topic 一律查此表拿 `name_zh`，不在 catalog 的 topic fallback 顯示原文 + `?`。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `event_topic` | TEXT PK | 主題字串（例 `'DEVICE_VIDEO_SIGNAL_LOST'`） |
| `name_zh` | TEXT NOT NULL | 中文顯示名 |
| `name_en` | TEXT NOT NULL | 英文顯示名 |
| `category` | TEXT NOT NULL | `'DEVICE'` 或 `'STATE'` |
| `is_fault` | INTEGER NOT NULL | 1 = 視為異常；0 = 資訊性（如 STATE_CONNECTING） |
| `sort_order` | INTEGER NULL | UI 顯示順序（唯一） |

### Seed 內容（17 筆，啟動時自動塞入）

| event_topic | 中文顯示 | category | is_fault | sort_order |
|---|---|---|---|---|
| DEVICE_VIDEO_SIGNAL_LOST | 影像訊號斷線（黑畫面） | DEVICE | 1 | 10 |
| DEVICE_TAMPERING | 破壞/遮蔽（場景改變） | DEVICE | 1 | 20 |
| DEVICE_COMMUNICATION_LOST | 通訊中斷 | DEVICE | 1 | 30 |
| DEVICE_CONNECTION_ERROR | 連線錯誤 | DEVICE | 1 | 40 |
| DEVICE_LONG_FAILED | 長期失敗（拔線） | DEVICE | 1 | 50 |
| DEVICE_DISCONNECTED | 斷線 | DEVICE | 1 | 60 |
| DEVICE_ANOMALY_START | 影像分析異常 | DEVICE | 1 | 70 |
| DEVICE_UNUSUAL_STARTED | 未預期活動 | DEVICE | 1 | 80 |
| STATE_DISCONNECTED | 斷線（攝影機無回應） | STATE | 1 | 110 |
| STATE_NOT_RESPONDING | 無回應（攝影機 hang） | STATE | 1 | 120 |
| STATE_FAILED | 連線失敗 | STATE | 1 | 130 |
| STATE_LONG_FAILED | 長期失敗（拔網路線） | STATE | 1 | 140 |
| STATE_BAD_CERTIFICATE | 憑證錯誤 | STATE | 1 | 150 |
| STATE_AUTH_FAILED | 認證失敗（帳密錯） | STATE | 1 | 160 |
| STATE_NETWORK_DOWN | 網路斷線 | STATE | 1 | 170 |
| STATE_TIMED_OUT | 連線逾時 | STATE | 1 | 180 |
| STATE_CONNECTING | 連線中（短暫狀態） | STATE | **0** | 200 |

### `cameras` 表加欄位（Phase 2.8）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `last_health_check_id` | INTEGER NULL | 反向指向最近一次 `image_health_checks.id`（NULL = 尚未檢查） |