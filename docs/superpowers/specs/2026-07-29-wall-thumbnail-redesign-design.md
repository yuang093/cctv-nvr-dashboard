# 設計：8444 /wall 相機牆視覺重構 + 背景縮圖快取

**日期**：2026-07-29
**狀態**：草案（待 user 確認）
**作者**：Claude（腦力激盪 → 規格）

## Context

8444 `/wall` 目前是純文字卡片 grid（見 `web/templates/wall.html`），只有：
- cam 名稱 + NVR 名稱 + 狀態 badge + IP + 24h 完整率
- 4 個篩選 tab（全部/在線/訊號中斷/無訊號）

user 參考 019 截圖，希望 /wall 改成「**視覺化相機牆**」：
- 每台 cam 一張縮圖卡（最近一張即時快照）
- 卡片左上角 status 圓點（綠/紅/琥珀對應 在線/訊號中斷/無訊號）
- 篩選 tab 後面加計數 `(N)`
- 預設排序：有問題的 cam 排最前面（訊號中斷最嚴重）
- 加「查看全部 N 支攝影機」按鈕（視覺一致；未來要上千支再加分頁）

**核心決策**：
- 縮圖來源：**背景批次抓 + DB 快取**（不即時打 NVR）
- 抓取時機：**跟 NVR_IMAGE_HEALTH 合併**（一次 HTTPS 同時拿 metadata + JPEG）
- 點縮圖：跳 `/devices/<id>`（保留現行為）

## 設計

### 1. 架構

**新 DB 表 `camera_snapshots`**：存每台 cam 最新的縮圖 JPEG bytes。

```sql
CREATE TABLE IF NOT EXISTS camera_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
    camera_id TEXT NOT NULL,
    jpeg_bytes BLOB NOT NULL,        -- 160x120 JPEG, ~5KB
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    captured_at TEXT NOT NULL,        -- UTC ISO8601
    UNIQUE (nvr_id, camera_id)
);
CREATE INDEX IF NOT EXISTS idx_camera_snapshots_captured_at
    ON camera_snapshots(captured_at DESC);
```

**Worker 端**（`batch_scan.py`）：`_image_health_check_loop` 內每台 cam 多一個 step。
**注意**：目前 batch_scan 沒有使用 MediaApiClient；本次在 `_image_health_check_loop` 內（或外層）實例化 `MpdMediaClient`（從 `web.clip_retrieval` 借用），傳給 loop 或在 loop 內 lazy 建立。MediaClient 初始化失敗（缺 session、缺 NVR_CLIPS_CLIENT 等）→ 跳過縮圖步驟、image_health 仍跑、不影響 scan 主流程。

**Web 端**（`web/app.py` + `web/db.py`）：`/wall` 從 `camera_snapshots` LEFT JOIN 拿縮圖。
**注意**：category（signal_lost/no_signal/online）原本由 `get_wall_cameras` 內的 subquery 計算。新 query `get_wall_cameras_with_snapshots` 必須 **保留同樣的 category 計算邏輯**（LEFT JOIN 最新未解 events → CASE），不能直接讀 `cameras` 表（沒這欄位）。建議實作：在 helper 內組 SQL 時把 category 的 CASE WHEN ... AS category 一起 SELECT 進來。

### 2. 元件

| 元件 | 檔案 | 責任 |
|---|---|---|
| **`db/sqlite_writer.py`** | 修改 | 加 `camera_snapshots` 表 CREATE + `upsert_snapshot()` 方法 |
| **`web/snapshot.py`** | 新檔 | 純函式 `compress_to_thumbnail(jpeg_bytes, size=(160, 120), quality=82)` — 從 `clips_app._compress_to_thumbnail` 抽出共用 |
| **`web/db.py`** | 修改 | 加 `get_wall_cameras_with_snapshots(filter)` + `get_wall_filter_counts()` |
| **`web/app.py`** | 修改 | `/wall` route 改呼叫新 helper + 注入 `counts` |
| **`web/templates/wall.html`** | 修改 | 卡片重寫：縮圖 + status 圓點 + 計數 tab + 「查看全部」按鈕 |
| **`batch_scan.py`** | 修改 | `_image_health_check_loop` 內每 cam 多抓縮圖步驟 |
| **`web/clips_app.py`** | 修改 | 改 import `compress_to_thumbnail` 從 `web.snapshot`（取代內部 `_compress_to_thumbnail`） |

### 3. 資料流

#### Worker（背景抓縮圖）

```
batch_scan (NVR_IMAGE_HEALTH=1)
  └─ for each cam in parallel (max_workers=8):
       ├─ 抓 JPEG via MediaClient.get_snapshot()  ← 跟 image_health 共用 HTTPS
       ├─ analyze image (blur/overexpose)         ← 既有邏輯不動
       ├─ compress_to_thumbnail(jpeg)              ← 新加（web.snapshot 共用）
       └─ writer.upsert_snapshot(nvr_id, cam_id, jpeg_thumb, w, h, now)
```

**失敗處理**：
- 抓 JPEG 失敗（cam 離線）→ try/except 包起來、log warning、不影響 scan 主流程
- 縮圖失敗（Pillow 問題）→ 跳過該 cam、繼續其他 cam

#### Web（讀 + 顯示）

```
GET /wall?filter=...
  └─ webdb.get_wall_cameras_with_snapshots(filter)
       → SELECT cam.*, snap.jpeg_bytes, snap.captured_at, latest_event_at
          FROM cameras c
          LEFT JOIN camera_snapshots snap ON snap.nvr_id = c.nvr_id AND snap.camera_id = c.device_id
          LEFT JOIN (latest event subquery) ... 
       ORDER BY 
         CASE category WHEN 'signal_lost' THEN 0 WHEN 'no_signal' THEN 1 WHEN 'online' THEN 2 ELSE 3 END,
         COALESCE(latest_event_at, '') DESC,
         camera_name ASC

  └─ webdb.get_wall_filter_counts()
       → 永遠回 4 類的全 DB 計數（不受 ?filter= 影響）
       → {"all": 48, "online": 41, "signal_lost": 3, "no_signal": 4}

  └─ render wall.html：
       - 卡片：<img src="data:image/jpeg;base64,..."> + status 圓點 + 名稱 + NVR 標籤
       - 沒快照：顯示 inline SVG camera icon（灰色）
       - 篩選 tab 後面加 (N)
       - 底部加「查看全部 N 支攝影機」按鈕（連到 /wall?filter=all）
```

### 4. UI 規格

**卡片樣式**（對齊 019 圖）：

```html
<a href="/devices/<device_id>" class="card wall-card">
  <div class="wall-thumb-wrapper">
    {% if snapshot_b64 %}
      <img src="data:image/jpeg;base64,{{ snapshot_b64 }}" 
           class="wall-thumb" alt="{{ camera_name }}">
    {% else %}
      <svg class="wall-thumb-placeholder">...camera icon...</svg>
    {% endif %}
    <span class="wall-status-dot 
                 bg-{{ 'success' if category == 'online' 
                       else 'danger' if category == 'signal_lost'
                       else 'warning' }}"></span>
  </div>
  <div class="card-body p-2">
    <div class="wall-cam-name">{{ camera_name }}</div>
    <div class="wall-nvr-label">{{ nvr_name }}</div>
  </div>
</a>
```

**篩選 tab 帶計數**：
```html
<a class="btn btn-sm btn-{{ 'primary' if filter_kind == 'all' else 'outline-secondary' }}">
  全部 ({{ counts.all }})
</a>
```

**底部按鈕**：
```html
<div class="text-center mt-4">
  <a href="?filter=all" class="btn btn-outline-primary">
    查看全部 {{ counts.all }} 支攝影機
  </a>
</div>
```

**Grid 規格**：
- 桌面：`row-cols-2 row-cols-md-3 row-cols-lg-4 row-cols-xl-6`（比現有 `1/2/3/4` 更密）
- 卡片寬高比：4:3（160×120 縮圖）
- Hover：translateY(-2px) + box-shadow（既有 `.wall-card` 樣式）

### 5. 排序規則

**預設排序**（`?filter=all` 或無 `?filter=`）：
```
訊號中斷（signal_lost）     ← 最嚴重，排最上
  ↳ 同類內：latest_event_at DESC（新→舊）
無訊號（no_signal）
  ↳ 同類內：latest_event_at DESC
在線（online）
  ↳ camera_name ASC（沒事件時間，用穩定順序）
```

**SQL CASE**：
```sql
ORDER BY
  CASE c.category
    WHEN 'signal_lost' THEN 0
    WHEN 'no_signal'   THEN 1
    WHEN 'online'      THEN 2
    ELSE 3
  END,
  COALESCE(c.latest_event_at, '') DESC,
  c.camera_name ASC
```

**篩選 + 排序同時作用**：選「在線」就只看 onine（內部按 cam_name 排）。

### 6. 錯誤處理

| 情境 | 行為 |
|---|---|
| NVR_IMAGE_HEALTH=0 沒跑過 | cam 卡顯示灰色相機占位 SVG（無縮圖） |
| 抓 JPEG 失敗（cam 離線 / NVR timeout） | 該 cam 跳過縮圖步驟、其他 cam 繼續 |
| Pillow 縮圖失敗 | 該 cam 跳過、其他 cam 繼續、log warning |
| DB 寫入失敗 | try/except 包、log warning、不影響 scan 結果 |
| 1000 cam 抓 JPEG 慢 | ThreadPoolExecutor max_workers=8 平行抓（跟 `clips_app` 同） |
| 沒 `media_client` 物件（測試環境） | 跳過縮圖步驟、image_health 仍跑 |

### 7. 測試計畫

**新測試檔**（不替換既有 `get_wall_cameras`，純加法）：

| 檔案 | 測試項目 |
|---|---|
| `tests/test_snapshot_thumbnail.py` | `compress_to_thumbnail`：JPEG → JPEG（size ≤ 10KB、160×120、format=JPEG、aspect preserved） |
| `tests/test_camera_snapshots_db.py` | `upsert_snapshot`：REPLACE INTO 正確性、UNIQUE 約束、`get_snapshot_for_camera` |
| `tests/test_wall_with_snapshots.py` | /wall 200、有 base64 `<img>`、沒快照的 cam 有 SVG fallback |
| `tests/test_wall_filter_counts.py` | tab 顯示 `(N)`、計數正確（不受 ?filter= 影響） |
| `tests/test_wall_sort_by_severity.py` | 預設排序：signal_lost > no_signal > online；同類內事件新→舊 |
| `tests/test_image_health_loop_saves_snapshot.py` | mock MediaClient，驗證 image_health loop 有呼叫 upsert_snapshot |
| `tests/test_clips_app_thumbnail_refactor.py` | `clips_app.py` 改 import `compress_to_thumbnail` 後功能仍正常（regression） |

**預期 pytest 從 646 → ~670（+24）**。

### 8. 契約文件更新

完成後同步更新：
- `database_schema.md` — 加 §10 `camera_snapshots` 表
- `class_interface.md` — 加 `web/snapshot.py` 介面 + `web.db.get_wall_cameras_with_snapshots()`

### 9. 不做的事（scope 控制）

- ❌ 不做縮圖分頁（user 說「將來上千支再說」）
- ❌ 不做縮圖時效過期顯示（user 沒要求）
- ❌ 不做點縮圖放大或即時播放（user 選 A：點跳 /devices）
- ❌ 不動 NVR 端 API 行為（純 backend）
- ❌ 不改 image_health 既有分析邏輯（只在 loop 內加一步）
- ❌ 不抽離 MediaClient 到共用模組（這次只複製必要的 step）

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| Flask template cache 沒重啟 | 強制重啟 server、用 curl 驗證 |
| 1000 cam DB 容量爆炸 | 5KB/cam × 1000 = 5MB，可接受；未來可加 `cleanup_old_snapshots()` 定期清舊資料 |
| NVR 被打爆（image_health + 縮圖） | 共用同一個 HTTPS response（一次拿兩個東西）、平行 8 workers |
| `camera_snapshots` 表不存在於舊 DB | `SqliteWriter.__init__` 已用 `CREATE TABLE IF NOT EXISTS`，首次啟動自動建 |
| `clips_app.py` 重構破壞既有功能 | `tests/test_clips_app_thumbnail_refactor.py` regression + 手動驗 8555 |

## Verification 順序

1. **靜態檢查**：`python -m py_compile web/snapshot.py web/db.py web/app.py db/sqlite_writer.py batch_scan.py web/clips_app.py`
2. **跑測試**：`pytest -q` → 預期 ~670 全綠
3. **重啟 8444**（Flask template cache）
4. **手動驗證 /wall**：確認縮圖卡、status 圓點、計數 tab、排序、「查看全部」按鈕
5. **跑 worker with 縮圖**：`NVR_IMAGE_HEALTH=1 python nvr_scanner.py` → 確認 `camera_snapshots` 寫入
6. **8444 dashboard** 不應被影響
7. **回歸 8555**：`NVR_CLIPS_CLIENT=mock python web/clips_app.py 8555` → /clips/snapshots 仍正常（共用 `compress_to_thumbnail`）