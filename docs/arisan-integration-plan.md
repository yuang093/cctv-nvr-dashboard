# 計畫藍圖：Arisan Gateway 功能吸收到 8444 v2 Web UI

**計畫日期**：2026-07-17
**先備文件**：`docs/arisan-features-review.md`
**User 已選**：① 影像健康巡檢 / ② 相機牆 / ③ 探索網段 / ⑤ 磁磚點擊跳轉；④ 基準線學習 → 不做

---

## Context

從 `Arisan-Gateway-使用手冊.pdf` 抽出 30+ 項功能，user 篩選 4 項要納入本輪開發。本計畫覆蓋這 4 項的設計與實作順序，完成後 8444 將具備：

- **影像健康巡檢** worker — 自動偵測每台攝影機的影像異常（模糊/過曝/欠曝/凍結），結果進 DB
- **相機牆** `/wall` — 一頁看完整體設備狀態 + 縮圖 grid
- **探索網段** `/devices/discover` — 自動掃 IP range 加 NVR
- **磁磚點擊跳轉** — Dashboard 磁磚（卡片）點下去帶 query string 跳篩選頁

## 不在本計畫內

- ❶ 基準線學習（user 明示不做；缺資料無法學）
- PDF 第 7 章裝置授權（GW-* 商業授權）、第 8 章雲端 / AI 模型 / 系統維護 / SNMP — 都不做
- 探索以外的 NVR CRUD 變更（沿用既有 `web/nvr_routes.py`）
- 8555 (clips app) — 不受影響，獨立運作

---

## 架構不變（user 強調）

| 契約 | 處置 |
|---|---|
| `AvigilonScanner` | 不改既有 method；可加新 `fetch_thumbnail(cam_id)` helper |
| `IDatabaseWriter` / `SqliteWriter` | 不動 |
| v1 worker batch_scan 主流程 | 不動；image-health 為附加階段 |
| v2 Web UI 唯讀 | 不動；image-health 由 worker 寫，UI 只讀 |
| Media API 連線 port 8443 | 沿用 REST session token |
| NVR config schema | 不動 |

**重要**：8444 worker 要從 NVR 抓 jpeg 縮圖。為避免跨 app 引用（MediaApiClient 在 8555），**新 helper 直接寫在 nvr_scanner.py**，30 行重複代價換架構乾淨。

## 既有功能保留清單（user 4 大功能 + 全部附屬全部保留）

**零刪減、零行為變更**——只在既有 4 個功能旁邊**新增** 4 個功能（磁磚/相機牆/探索/影像健康）。

| 既有功能 | 既有 route | 既有 template | 本計畫處置 |
|---|---|---|---|
| **掃描記錄** | `/runs`、`/runs/<run_id>` | `runs_list.html`、`run_detail.html` | ✅ 完全不動（含「▶ 立即掃描」按鈕） |
| **故障相機總覽** | `/abnormal` | `abnormal.html` | ✅ 完全不動 |
| **下載歷史報告** | `/abnormal/export.pdf`、`/reports`、`/reports/download/<run_id>` | `reports_list.html` | ✅ 完全不動（生成的 PDF/CSV 一樣） |
| **異常事件記錄查詢** | `/events`、`/query`（GET/POST） | `events_list.html`、`query.html` | ✅ 完全不動（含 status 篩選） |
| **NVR 清單** | `/nvrs` | `nvrs_list.html` | ✅ 完全不動 |
| **NVR 新增** | `GET|POST /nvrs/new` | `nvr_form.html` | ✅ 完全不動 |
| **NVR 編輯** | `GET|POST /nvrs/<id>/edit` | `nvr_form.html` | ✅ 完全不動 |
| **NVR 停用/啟用** | `POST /nvrs/<id>/toggle` | （在 `nvrs_list.html` 內） | ✅ 完全不動 |
| **NVR 刪除** | `POST /nvrs/<id>/delete` | （在 `nvrs_list.html` 內 Modal） | ✅ 完全不動 |
| **NVR 匯入** | `GET|POST /nvrs/import`、`/nvrs/import/template.csv|json` | `nvr_import.html` | ✅ 完全不動 |
| **NVR 匯出** | `GET /nvrs/export.csv`、`/nvrs/export.json` | （直接下載） | ✅ 完全不動 |
| **NVR 測試連線** | `POST /nvrs/test-connection` | （JSON API） | ✅ 完全不動 |
| **Webhook 推播（Slack/Teams）** | `webhook.py`（Flask 之外） | — | ✅ 完全不動 |
| **NVR 連線失敗追蹤** | `nvr_failure_log` 表 + `log_nvr_failure()` + `dashboard/runs_list` 顏色化 | `run_detail.html`、`runs_list.html` | ✅ 完全不動 |

### 唯一既有 route 修改

| route | 修改幅度 | 理由 |
|---|---|---|
| `/` (dashboard) | **微幅** — 4 個磁磚變 anchor，href 帶 query string | 實作「磁磚點擊跳轉」這是 user 選的新功能 |

### 新增 4 個功能（與既有不重疊）

| 新功能 | 新 route | 與既有關係 |
|---|---|---|
| 磁磚點擊跳轉 | （改造 `/`） | 修改既有 dashboard |
| 相機牆 | `/wall`（新） | 「視覺化」監控層；不取代 `/abnormal`（總覽）、`/runs`（記錄） |
| 探索網段 | `/devices/discover`（新） | 輔助新增 NVR，匯入流程與 `/nvrs/import` 並行 |
| 影像健康巡檢 | `/health/cameras/<id>`（新）+ `/devices/<id>`（新） | 輔助檢視；不取代 `/runs`、`/events` |

### 新舊路由語義對照（避免混淆）

```
/nvrs           ← NVR CRUD（NVR 的管理）
/nvrs/new       ← 新增單一 NVR
/nvrs/import    ← CSV/JSON 批次匯入 NVR
/nvrs/export.*  ← 匯出 NVR 清單

/devices        ← （新）跨 NVR 設備（cam）監控總覽表
/devices/<id>   ← （新）單台 cam 詳情 + 健康卡
/devices/discover  ← （新）未知網段探索

/wall           ← （新）相機牆縮圖 grid
/health/cameras/<id>  ← （新）單台 cam 健康歷史
```

**`/nvrs` 完全不受影響**，所有 NVR CRUD 入口照舊。

### user 決策（2026-07-17）

`/devices` 路由**保留**（方案 B 而非 A）

**決策理由（user 提供）**：未來會有多台 NVR，因此 `/devices` 作為跨 NVR 的「設備總覽表」對於多 NVR 場景有意義：

- 統一篩選所有 NVR 下的 cam（含不正常 / 離線 / 影像異常）
- 可依 NVR / 狀態 / 健康分頁/排序
- 跟 `/wall`（視覺化 grid）互補；不會做同樣的事

**實作時注意事項**：
- `/devices` 預設要分頁（page size 50）
- 提供 filter：NVR / 狀態（online/signal_lost/no_signal/anomaly）/ 影像健康（with/without issues）
- 大量 NVR 時，列表 query 加 LIMIT/OFFSET 並 index（`cameras.nvr_server_id`、`cameras.connection_status`）



---

## 資料模型（DB schema 變更）

### 新增表 1：`image_health_checks`

```sql
CREATE TABLE image_health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,           -- Avigilon camera device_id
    nvr_server_id INTEGER,             -- FK -> nvr_servers.id（建議加 INDEX）
    checked_at_utc TEXT NOT NULL,      -- ISO8601 UTC

    -- 偵測結果：JSON 包各種指標分數（0~1）+ flag
    -- 結構範例：
    -- {"blur": {"score": 0.12, "is_blurry": true},
    --  "mean_luma": {"score": 0.04, "is_under": true},
    --  "frozen_diff": {"score": 0.001, "is_frozen": true}}
    metrics_json TEXT NOT NULL,
    flags_json TEXT NOT NULL,           -- 同上但只列被觸發的（如 ["blurry","under"]）

    -- 觸發事件寫進 events 表（status='pending', kind='IMAGE_*'）
    triggered_event_ids TEXT,           -- JSON array

    FOREIGN KEY (nvr_server_id) REFERENCES nvr_servers(id)
);
CREATE INDEX idx_health_cam_time ON image_health_checks(camera_id, checked_at_utc DESC);
CREATE INDEX idx_health_nvr ON image_health_checks(nvr_server_id);
```

### 新增表 2：`discover_sessions`

```sql
CREATE TABLE discover_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    cidr TEXT NOT NULL,                -- e.g. "192.168.0.0/24"
    results_json TEXT NOT NULL,        -- JSON array of {ip, open, nvr?, added_to_db?}
    status TEXT NOT NULL                -- 'running' | 'completed' | 'failed'
);
```

### 改動表 1：`cameras`

加一欄（可選遷移）：
```sql
ALTER TABLE cameras ADD COLUMN last_health_check_id INTEGER;
```
（注意既有資料保留；新欄位 nullable）

### 新增表 3：`event_kind_catalog`（17 種事件主題，user 明示全部保留）

```sql
CREATE TABLE event_kind_catalog (
    event_topic TEXT PRIMARY KEY,      -- e.g. 'DEVICE_VIDEO_SIGNAL_LOST'
    name_zh TEXT NOT NULL,            -- e.g. '影像訊號斷線（黑畫面）'
    name_en TEXT NOT NULL,            -- e.g. 'Video signal lost (black screen)'
    category TEXT NOT NULL,           -- 'DEVICE' | 'STATE'
    is_fault INTEGER NOT NULL,        -- 1 = 視為異常；0 = 資訊性（如 STATE_CONNECTING）
    sort_order INTEGER                -- UI 顯示順序
);
```

完整內容（user 提供對照表，列入 seed 資料）：

| event_topic | 中文顯示 | 分類 | is_fault |
|---|---|---|---|
| DEVICE_VIDEO_SIGNAL_LOST | 影像訊號斷線（黑畫面） | DEVICE | 1 |
| DEVICE_TAMPERING | 破壞/遮蔽（場景改變） | DEVICE | 1 |
| DEVICE_COMMUNICATION_LOST | 通訊中斷 | DEVICE | 1 |
| DEVICE_CONNECTION_ERROR | 連線錯誤 | DEVICE | 1 |
| DEVICE_LONG_FAILED | 長期失敗（拔線） | DEVICE | 1 |
| DEVICE_DISCONNECTED | 斷線 | DEVICE | 1 |
| DEVICE_ANOMALY_START | 影像分析異常 | DEVICE | 1 |
| DEVICE_UNUSUAL_STARTED | 未預期活動 | DEVICE | 1 |
| STATE_DISCONNECTED | 斷線（攝影機無回應） | STATE | 1 |
| STATE_NOT_RESPONDING | 無回應（攝影機 hang） | STATE | 1 |
| STATE_FAILED | 連線失敗 | STATE | 1 |
| STATE_LONG_FAILED | 長期失敗（拔網路線） | STATE | 1 |
| STATE_BAD_CERTIFICATE | 憑證錯誤 | STATE | 1 |
| STATE_AUTH_FAILED | 認證失敗（帳密錯） | STATE | 1 |
| STATE_NETWORK_DOWN | 網路斷線 | STATE | 1 |
| STATE_TIMED_OUT | 連線逾時 | STATE | 1 |
| STATE_CONNECTING | 連線中（短暫狀態） | STATE | 0 |

### scanner 行為改為完整保留

- **不再只用 8 種關鍵字過濾** — 改為：
  - DEVICE_* 8 種全部視為異常（既有關鍵字全保留，**不擴充也不縮減**）
  - STATE_* 視為**狀態事件** —— 全部寫入 events 表（kind 對齊 catalog）
    - 9 種 STATE_* 全部保留（包含 8 個 is_fault=1 + 1 個 is_fault=0 的 STATE_CONNECTING）
    - STATE_CONNECTING 仍寫進 events 表，但 UI 預設不列入「開啟中警示」磁磚（顯示時仍可見，加 is_fault=0 badge）
    - STATE_* 同時更新 `cameras.connectionStatus.state` 欄位（既有邏輯）
- 17 種以外的 topic：仍寫進 events 表（fallback 顯示原文 + ?），不丟

### Web UI 顯示一律用中文（catalog 為單一真相）

- `/events` 列表：kind 欄位顯示 `name_zh`
- `/abnormal` 報表：同上
- `/devices/<id>` 健康輪廓：標示每一種被觸發的偵測用中文顯示
- 排序：依 catalog.sort_order，未在 catalog 內的 topic fallback 顯示原文 + (?)
- 篩選列（events 頁的 filter）：用 catalog 列出所有 17 種含中文 label + 各自的 event count
- 17 種以外的 topic：仍顯示在列表，但加 (?) 標記；可由 admin 補進 catalog


### events 表 — 與既有 DEVICE_TAMPERING 整合

- **既有** `ABNORMAL_KEYWORDS` 內的 `DEVICE_TAMPERING` 已涵蓋**遮擋 + 位移**，不需要任何改動
- 4 種自製偵測（blur/over/under/frozen）觸發時，worker 在 image-health 階段檢查 metrics_json；若新觸發則寫 1 條 events（`status='pending'`、`source='image_health'`），並把 id 寫進 `image_health_checks.triggered_event_ids`

### 等同的兩條路徑

```
遮擋 / 位移：
  NVR 推送 DEVICE_TAMPERING → 既有 scan() → events 表（kind='DEVICE_TAMPERING'）

模糊 / 過曝 / 欠曝 / 凍結：
  worker image-health 階段 → 寫 image_health_checks + 觸發 events (source='image_health')
```

Web UI 對兩條路徑一視同仁，從 events 表讀，差別只在 `source` 欄位。

**不新增新的 `kind` 列舉**，沿用 `DEVICE_TAMPERING`（既有意義）與自由文字 source。


### 既有 DB schema migration

- 寫 `web/db.py` 內既有 `init_db()` 加 CREATE TABLE IF NOT EXISTS
- `ALTER TABLE cameras ADD COLUMN last_health_check_id` 用 try/except 容錯（既有資料庫可能已存在）
- 更新 `database_schema.md` 反映新表 + 欄位

---

## API 路由（8444 web/app.py 新增）

| Route | Method | 用途 | 來源 |
|---|---|---|---|
| `/wall` | GET | 相機牆 grid | 新 |
| `/wall` | GET(?filter=online\|signal_lost\|no_signal) | 篩選 | 新（query string） |
| `/devices/discover` | GET | 探索表單 | 新 |
| `/devices/discover` | POST | 執行探索（CIDR） | 新 |
| `/devices/discover/<id>` | GET | 結果明細頁 | 新 |
| `/devices/discover/<id>/add` | POST | 把該 session 內某 IP 加成 NVR | 新 |
| `/health/cameras/<camera_id>` | GET | 單台 cam 健康輪廓（last N checks） | 新 |
| `/health/cameras/<camera_id>/recheck` | POST | 觸發當下重檢（用現有 idle thread） | 新 |
| `/devices` | GET | 設備清單（與 NVR 清單分開） | 新 |
| `/devices/<device_id>` | GET | 單台設備詳情 + 影像健康卡 | 新 |
| `/?tile=...` | GET | dashboard 帶 tile context（前端用） | 修改：磁磚加 `<a href>` |

**與既有重疊**：
- `/` 既有（dashboard）會**修改**：4 個磁磚變 anchor，href 帶 query string 跳對應篩選
- 既有 `/abnormal`、`/query`、`/runs` 不動

---

## UI 變更（web/templates/）

### 新增 templates

#### `web/templates/wall.html`
- Bootstrap 5.3.2 + dark mode toggle（沿用 8444 既有）
- Grid 4 欄 × N 列（cam 數量）
- 每張卡：
  - 上半：jpeg 縮圖
  - 下半：cam 名稱 + 狀態 pill
  - 異常卡：取代縮圖（紅卡「訊號中斷」/ 琥珀卡「無訊號」/ 灰卡「影像異常」）
- 點縮圖 → 跳 `/devices/<device_id>`
- 篩選按鈕在頂端（與 events 頁同風格）

#### `web/templates/discover.html`
- 表單：CIDR 輸入 + 連接埠（預設 8443）+ 按鈕「開始掃描」
- 跑時：progress bar（每 IP 一格）
- 結果 table：IP / Port open / Avigilon 偵測 / NVR name / 操作（加入清單 / 略過）

#### `web/templates/device_detail.html`
- 頂部：設備基本資訊（NVR、名稱、IP、最後檢查時間）
- 中段：縮圖（live jpeg）
- 下段：**影像健康輪廓**小卡（5 個 metric + AI 白話說明）
  - 模糊 / 過曝 / 欠曝 / 凍結 / 遮擋（v1 沒有遮擋）
  - AI 白話：「偵測到過曝：mean luma = 0.92，建議檢查攝影機是否面向強光」

### 修改 templates

#### `web/templates/dashboard.html`
- 「全機統計」4 磁磚變 `<a>` 帶 query string：
  - 在線 N → `/devices?filter=online`
  - 訊號中斷 → `/wall?filter=signal_lost`
  - 無訊號 → `/wall?filter=no_signal`
  - 開啟中警示 → `/events?status=pending`
  - 影像異常（新增 1 個）→ `/events?status=pending&image_health=true`
- 新增第 5 個磁磚「影像異常」(count from image_health_checks)

#### `web/templates/nvrs_list.html`
- 既有 NVR CRUD 表格
- **新增行**：每台 NVR row 加「▶ 立即掃描影像健康」按鈕（POST 觸發 worker 對該 NVR 重檢）
- 不直接影響 8555（保持兩個 app 獨立）

---

## Worker 變更

### 新 helper：`nvr_scanner.py`

```python
def fetch_thumbnail(self, camera_id: str, t: datetime | None = None) -> bytes | None:
    """從 NVR 抓一張 jpeg 縮圖 bytes（走 Media API port 8443，REST session 沿用）。"""
```

實作：GET `/mt/api/rest/v1/media?format=jpeg&cameraId=<id>&t=<iso>` → bytes

### 新 helper：`image_health.py`（新檔）

```python
@dataclass
class ImageHealthResult:
    camera_id: str
    checked_at_utc: str
    metrics: dict        # 完整 metrics_json
    flags: list[str]     # 觸發的 ['blurry','under','over','frozen'] 子集

def analyze_image(jpeg_bytes: bytes) -> dict:
    """單張影像：blur + mean luminance"""
    # Pillow: load → grayscale → numpy-free 計算
    # blur: Laplacian variance approximation via convolution（簡單版用邊緣像素變異）
    # mean_luma: sum/length
    return {"blur_var": float, "mean_luma": float}

def is_frozen(jpeg_a: bytes, jpeg_b: bytes) -> float:
    """兩張影像 mean abs diff，< 某門檻 = frozen"""
```

### 新流程：batch_scan 中插一段

```
batch_scan (現有) → 寫 events/DEVICE_*
              ↓
image_health_check_loop (新) → 抓 jpeg + 寫 image_health_checks → 若觸發新事件則寫 events 表 IMAGE_*
```

### 排程

- `batch_scan` 跑完一輪後接著跑 image-health（`run_worker.sh` 不改）
- 每台 cam 抓 2 張 jpeg、間隔 5 秒（frozen 偵測）
- 預估每台 6-8 秒：50 台 cam × 8s = 7 分鐘，可接受

---

## 影像分析偵測（6 種：自製 4 種 + ACC 內建 2 種）

| 偵測 | 演算法 / 來源 | 觸發條件 | 資料表 |
|---|---|---|---|
| **模糊** | Laplacian variance approximation（簡單 3×3 kernel 算像素變異；不引 OpenCV） | var < 30 | 自製寫 `image_health_checks.metrics_json` |
| **過曝** | mean luminance > 0.85 | 強光場景 | 同上 |
| **欠曝** | mean luminance < 0.15 | 全黑場景 | 同上 |
| **凍結** | 兩張 5 秒間隔影像的 mean abs pixel diff < 5 | 需要 fetch 兩張 jpeg | 同上 |
| **遮擋** | NVR ACC 內建 analytics | `DEVICE_TAMPERING` event | **既有 events 表**（scan() 已收） |
| **位移（畫面被改向）** | 同上 — ACC 把「場景改變」歸在同一主題 | `DEVICE_TAMPERING` event | 同上 |

### 重要修正

**「遮擋」與「位移」對應 ACC 的同一個 `DEVICE_TAMPERING` 事件**（場景改變）——這是 NVR 內建 analytics，已被我們 `AvigilonScanner` 既有 `ABNORMAL_KEYWORDS` 捕獲，**不必自製影像分析**：

```python
ABNORMAL_KEYWORDS = (
    "DEVICE_VIDEO_SIGNAL_LOST",
    "DEVICE_TAMPERING",    # ← 涵蓋 遮擋 + 位移（場景改變）
    "DEVICE_COMMUNICATION_LOST",
    ...
)
```

### Web UI 整合

`/devices/<id>` 影像健康小卡、dashboard 影像異常磁磚、`/events?kind=device_tampering` 篩選 —— 都從 `events` 表讀 `DEVICE_TAMPERING`，與 4 種自製偵測並列顯示。

門檻值：先用常數啟動，後續可從 `nvr_config.json.scan_settings.image_health` 讀；前 2 輪收集資料後再校準。

---

## 探索網段（POST /devices/discover）

### 流程

```
POST /devices/discover {"cidr": "192.168.0.0/24"}
  ↓
寫 1 條 discover_sessions (status='running')
  ↓
用 ipaddress 算所有 IP（/24 = 254 個）
  ↓
concurrent.futures ThreadPoolExecutor (max 8 workers) 探測
  ↓
對每 IP:
    try AvigilonScanner minimal probe（不需 CC/SHA，只要 GET / 確認 401 or 200）
    若有 Avigilon 標頭 → 加 result entry
  ↓
更新 discover_sessions (status='completed', results_json=...)
  ↓
前端 poll /devices/discover/<id>
```

### 探測方式

不用 SHA-256 auth（太慢），只用：
```python
requests.get(f"https://{ip}:8443/", timeout=3, verify=False)
# 看 response 是否有 "Avigilon" 字串、status code、SSL cert subject
```

255 IP × 3s timeout / 8 workers = ~96 秒。

**安全考量**：跳過既有的 NVR IP（不重複探測）；如使用者輸入超 /16 拒絕（防 DoS 自身）。

---

## pytest 規劃（預期 +X 條測試）

| Test file | 新增測試 |
|---|---|
| `tests/test_image_health.py` | analyze_image 四種 metric 邊界值；is_frozen 兩張相同/不同 |
| `tests/test_image_health_integration.py` | 用 mock jpeg bytes → worker 寫 DB → 觸發 events |
| `tests/test_wall_routes.py` | `/wall` 三種 filter 狀態；異常 cam 用紅/琥珀卡 |
| `tests/test_discover_routes.py` | POST CIDR → sessions 建；/add 路由把 IP 加 nvr_servers |
| `tests/test_dashboard_tile_clicks.py` | 4 個磁磚 src 各帶對應 href |
| `tests/test_device_detail_route.py` | 顯示 image_health 小卡，5 個 metric + AI 說明 |
| `tests/test_event_kind_catalog.py` | seed 17 筆；未知 topic fallback 顯示原文；STATE_CONNECTING 不計入 pending count |
| `tests/test_event_label_i18n.py` | events 列表 / abnormal 報表 / dashboard 磁磚全部顯示 name_zh |

預計從 412 → ~445 條測試。

---

## 實作順序（4 階段，總估時 ~5-6 天）

| 階段 | 工作 | 估時 | 驗證 |
|---|---|---|---|
| **#1 磁磚點擊跳轉** | dashboard.html 4 個磁磚加 `<a>`；5 條新測試 | 0.5 天 | curl / + grep href |
| **#2 DB schema** | 加 CREATE TABLE + ALTER TABLE + 既有 init 容錯；更新 database_schema.md | 0.3 天 | pytest 既有通過 + 新表存在 |
| **#3 影像分析核心** | `image_health.py` 純函式 + Pillow-only；15 條單元測試 | 1 天 | pytest |
| **#4 worker 整合** | nvr_scanner 加 fetch_thumbnail；batch_scan 接 image_health 階段 | 1 天 | 真 NVR 一輪 |
| **#5 Web 路由 + UI** | `/health/cameras/<id>`、`/wall`、`/devices`、`/devices/<id>`、`/devices/discover` 共 5 個 route + 4 個 template | 1.5 天 | curl 5 條路 |
| **#6 探索網段** | `/devices/discover` POST + 並行探測；session 儲存 | 0.5 天 | mock NVR 探 |

完成後單獨重啟 8444 server。

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| NVR 抓 jpeg 慢 / timeout | 個別 cam 失敗不中斷整批；單台失敗 log warn |
| Pillow variant matrix（M1 / Windows） | 用 Pillow >= 10.0 標準 API，不依賴特定加速 |
| 影像分析誤報（場景本來就模糊） | 不觸發 events 表（只寫 image_health_checks.flags_json），events 觸發要 N 輪確認 |
| 探索網段觸發 NVR lockout | 探測只用 GET / 不打 /login；timeout 短 |
| 凍結偵測需 2 張 jpeg 並延遲 | worker 順序執行同台時 sleep 5s，避免併發抓同台 |
| 既有 dashboard 卡片被換成 anchor | 用 `<a class="card-tile">` 包既有 div，CSS transition 微調；驗視覺無 regression |
| **events 表膨脹**（17 種全收 + STATE 持續推送） | 既有 retention 政策不變；catalog.is_fault=0 的（STATE_CONNECTING）UI 可一鍵 hide |
| **catalog 缺漏新 topic 時 fallback** | UI 顯示原文 + (?)，admin 可手動補進 catalog（後續可做「建議補入」按鈕，這輪不做） |

---

## 不做（scope 控制）

- ❌ 自製遮擋 / 位移影像分析（用既有 `DEVICE_TAMPERING` event）
- ❌ 基準線學習（user 明示不做；缺資料）
- ❌ Dashboard 加 Chart.js 視覺化（nice-to-have，等下輪）
- ❌ 8555 clips app 同步（clips 獨立）
- ❌ 用真 AI 模型（OpenCV / LLM）；這輪只用規則 + Pillow
- ❌ 設備清單的「廠牌自動組 RTSP」（我們只支援 Avigilon）
- ❌ Cloud、License — 與本專案無關

---

## 完成定義（Definition of Done）

- [ ] 4 個功能全部實作完成
- [ ] **17 種事件主題全部保留並顯示中文**
  - [ ] `event_kind_catalog` seed 完成 17 筆
  - [ ] Web UI 跑 1 輪 `python nvr_scanner.py` 後，UI 能列出 NVR 推送的所有 topic（含 STATE_*），每種中文顯示
  - [ ] STATE_CONNECTING 仍進 events 表但顯示時加「資訊」徽章，不計入「開啟中警示」磁磚
- [ ] pytest 全部綠（含新增測試）
- [ ] 真 NVR 上手動跑 `python nvr_scanner.py` + image-health 各檢查一次有資料進 DB
- [ ] curl `/wall` `/devices/discover` `/health/cameras/<id>` 都回 200
- [ ] dashboard 4 個磁磚點下去跳對的篩選頁
- [ ] `database_schema.md` 同步更新（event_kind_catalog 表）
- [ ] 下一個 checkpoint 寫出來（v8）
