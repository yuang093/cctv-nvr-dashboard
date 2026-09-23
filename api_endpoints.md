# Avigilon API 端點參考 (API Endpoints)

> **狀態（2026-06-23 實測確認）**：ACC 8.7+ **所有端點都位於 `/mt/api/rest/v1/` 命名空間**。
>
> 舊版「混合命名空間」假設（部分 root level + 部分 `/mt/api/rest/v1/`）已在實機驗證中淘汰。
> 正確做法：**所有端點一律加上 `/mt/api/rest/v1/` 前綴**。

Base URL: `https://<server_ip>:8443`

---

## 1. Avigilon NVR API（ACC 8.7+）

> 詳細規格（認證 / 欄位）見 `Avigilon_API_Reference.md`。
> 本文件只列出**路徑與回傳形狀**，介接細節以規格書為準。

### 1.1 完整路徑速查表

| 端點 | 方法 | 完整路徑 | 回傳包裝 |
|---|---|---|---|
| 登入 | `POST` | `/mt/api/rest/v1/login` | `{"status": "success", "result": {"session": "..."}}` |
| 伺服器 ID | `GET` | `/mt/api/rest/v1/server/ids?session=<token>` | `[{"serverId": "..."}]` 或包裝 |
| 攝影機列表 | `GET` | `/mt/api/rest/v1/cameras?session=<token>&serverId=<id>&pageSize=<n>` | `{"cameras": [...]}` 或 list |
| 事件搜尋（ACTIVE） | `GET` | `/mt/api/rest/v1/events/search?session=<token>&serverId=<id>&queryType=ACTIVE&pageSize=<n>` | `{"events": [...]}` 或 list |
| 事件字典 | `GET` | `/mt/api/rest/v1/event-subtopics?session=<token>&serverId=<id>` | `["DEVICE_VIDEO_SIGNAL_LOST", ...]` |

### 1.2 認證流程

所有需要 session 的端點都帶 `?session=<token>` query 參數：

1. `POST /mt/api/rest/v1/login` 帶 SHA-256 `authorizationToken` → 拿到 session token
2. 後續 GET 帶 `?session=<token>` 參考

> ⚠️ **不要用 HTTP Basic Auth**，ACC 8.7+ 強制要求動態 `authorizationToken`，否則回 `403 Unknown reason`。

### 1.3 回應格式：兩種都會出現

ACC 8.7+ 的回應可能是：

1. **包裝型**：`{"status": "success", "result": [...]}`（大多數）
2. **裸 list**：`[{"serverId": "..."}]`（部分 server/ids 場景）
3. **裸 dict-of-objects**：`{"id1": {...}, "id2": {...}}`（部分 cameras 場景）

本專案 `unwrap_response()` 與 `_extract_list()` 已相容這三種（見 `nvr_scanner.py`）。

### 1.4 常見錯誤對照

| HTTP Status | 意義 | 處置 |
|---|---|---|
| 401 / 403 | 帳密錯誤或無 API 權限 | 檢查 `userNonce`/`userKey`；確認該帳號有 External API 權限 |
| 404 | 路徑不對 | 確認**所有**端點都帶 `/mt/api/rest/v1/` 前綴 |
| 502 / 503 | NVR 服務未啟動或過載 | 稍後重試 |
| Timeout | 網路問題 | 調高 `timeout` 參數；檢查防火牆 |

---

## 1.5 NVR Media API（影片片段調閱，Phase 2.7）

> 來源：`web/clip_retrieval.py` + `docs/media-api-research.md`（PDF 已驗證規格）。
> 用途：取得單張 snapshot 或 30 秒 fragment MP4 串流（給其他部門調閱用）。
> Port：**8443**（**與 REST API 同一個 port**，不是 8555；8555 是 clip Web UI 自己）。
> Auth：沿用 `/login` 的 `?session=<token>`，無獨立 auth。

### 1.5.1 端點

```
GET https://<host>:8443/mt/api/rest/v1/media
    ?session=<SESSION>
    &cameraId=<CAMERA_ID>
    &format=<mpd|fmp4|jpeg|json|webm|spkc>
    [&t=<ISO8601 timestamp>]            # 可選；`live` 或 ISO 8601
```

### 1.5.2 6 種 format

| format | 用途 | 適用 |
|---|---|---|
| `mpd` | DASH manifest（XML 多個 Representation 帶 BaseURL）| ✅ 主流程：抓 manifest → BaseURL + `t=` → 30s clip |
| `fmp4` | Fragmented MP4 單 GET 串流 | ✅ 備用：直接帶 `&t=<time>` 拿 mp4 |
| `jpeg` | 單張 snapshot | ✅ 預覽階段（給 clip Web UI 縮圖 grid）|
| `json` | bounding boxes / AI feature vectors | ❌ 不適用 |
| `webm` | audio stream | ❌ 不適用 |
| `spkc` | 查 speaker codecs | ❌ 不適用 |

### 1.5.3 推薦的 30s 調閱流程

**路徑 A（DASH MPD，兩步）：**
```
Step 1: GET .../media?format=mpd → 回 MPD XML（多個 Representation）
Step 2: 選最低 bandwidth Representation → 抓其 <BaseURL> + f"&t=<ISO8601 start>"
        → 回 fragmented MP4 stream
```

**路徑 B（fmp4 直取，一步）：**
```
GET .../media?format=fmp4&t=<ISO8601 start> → 回 H.264 fragmented MP4 stream
```

### 1.5.4 Clip Web UI Routes（port 8555）

> 來源：`web/clips_app.py`（2026-07-06 完成）。
> Base URL: `http://127.0.0.1:8555`（`NVR_CLIPS_PORT` 環境變數覆寫）
> 環境變數：`NVR_CLIPS_CLIENT=mock` 可切到 MockMediaClient（不打真 NVR）

| Method | Path | 用途 | 來源 |
|---|---|---|---|
| GET | `/` / `/clips` | 兩段式 UI 頁（NVR + 時間 → 縮圖 grid → 點擊載 30s 影片）| `web/templates/clips.html`（Week 6 #018 改由 `web/blueprints_clips/pages_bp.py` 提供） |
| GET | `/clips/nvrs` | JSON：所有 NVR 清單（給下拉用）| `web/db.py::get_nvrs`（Week 6 #018 → `web/blueprints_clips/media_bp.py::nvrs`）|
| GET | `/clips/cameras` | ?nvr_id= → JSON cameras list | `web/db.py::list_cameras_for_nvr`（Week 6 #018 → `web/blueprints_clips/media_bp.py::cameras`）|
| GET | `/clips/snapshots` | ?nvr_id=&t= → 並行抓 N 台相機 jpeg，縮圖後 JSON | `web/clips_helpers.fetch_snapshots_parallel`（Week 6 #018 → `web/blueprints_clips/media_bp.py::snapshots`）|
| POST | `/clips/fetch` | {nvr_id, camera_id, start, end} → stream mp4 bytes | `web/blueprints_clips/media_bp.py::fetch_clip`（Week 6 #018）|
| POST | `/clips/fetch_sync` | 多 cam 同步 multipart（含 stale probe）| `web/blueprints_clips/media_bp.py::fetch_sync`（Week 6 #018）|
| GET | `/clips/coverage` | 錄影覆蓋熱區頁（Spec F）| `web/blueprints_clips/coverage_bp.py::coverage`（Week 6 #018）|
| GET | `/clips/coverage/data` | 熱區 JSON（Spec F）| `web/blueprints_clips/coverage_bp.py::coverage_data`（Week 6 #018）|

**重要**：port 8555 是 **clip Web UI 自己的部署 port**；NVR 端 Media API 仍在 port **8443**。

---

## 2. Web UI Routes（v2，本專案 Flask app）

> 來源：`web/app.py`（v2 雛形，2026-06-29 完成；Week 7 Issue #022 改由 OpenAPI 自動產生）。
> 設計：**唯讀**（`PRAGMA query_only = ON`）不與 worker 競爭 DB。
>
> **Week 7 起（Issue #022）**：本節路由表**已由 OpenAPI 自動產生**取代。
> 詳細 schema 與 parameters 請見：
> - Swagger UI：`http://127.0.0.1:8444/apidocs/`
> - OpenAPI JSON：`http://127.0.0.1:8444/apispec_1.json`
> - 8555 clips：`http://127.0.0.1:8555/apidocs/`
>
> 路由 source-of-truth 在 `web/blueprints/*.py`（8444）與 `web/blueprints_clips/*.py`（8555），
> YAML 規格在 `web/openapi/{dashboard,clips}/*.yml`。修改 code 後會自動反映在 Swagger UI。

Base URL: `http://127.0.0.1:8444`（預設；可用 `NVR_WEB_HOST` / `NVR_WEB_PORT` 環境變數覆寫）

### 2.1 路由表（**已由 OpenAPI 自動產生取代**）

> **Week 7 Issue #022 起**：本節路由表由 flasgger 自動產生（45 條 routes：35 dashboard + 10 clips），
> 手寫維護成本高且易漂移。**Source of truth**：
>
> - **Swagger UI**：`http://127.0.0.1:8444/apidocs/`（dashboard）+ `http://127.0.0.1:8555/apidocs/`（clips）
> - **OpenAPI YAML 規格**：`web/openapi/dashboard/*.yml` + `web/openapi/clips/*.yml`
> - **Routes 定義**：`web/blueprints/*_bp.py`（8444）+ `web/blueprints_clips/*_bp.py`（8555）
>
> 本表僅保留**高層次對照**，完整 schema 與 parameters 請見 Swagger UI。

| App | Port | 業務領域 | 條數 | Source |
|---|---|---|---|---|
| dashboard | 8444 | dashboard / runs / nvrs / scan / devices | 35 | `web/blueprints/{dashboard,runs,nvrs,scan,devices}_bp.py` |
| clips | 8555 | pages / coverage / media | 10 | `web/blueprints_clips/{pages,coverage,media}_bp.py` |

### 2.2 Query String 參數

| Endpoint | 參數 | 預設 | 說明 |
|---|---|---|---|
| `/runs` | `page` | `1` | 頁碼（≥1） |
| `/runs` | `per_page` | `20` | （未在 URL 暴露，固定 20） |
| `/events` | `hours` | `24` | 過去幾小時的事件 |
| `/events` | `nvr_id` | — | DB 內部整數 ID（過濾特定 NVR） |
| `/events` | `topic` | — | 主題關鍵字（含 DEVICE_*/STATE_*） |
| `/events` | `status` | `all` | `all` / `open`（resolved_at IS NULL） / `resolved`（**Phase 1 Step 3a**） |
| `/query` | `sql`（POST body） | — | 唯讀 SELECT 字串 |

### 2.3 歷史 PDF 報告歸檔（v2.7）

| 項目 | 規格 |
|---|---|
| 觸發時機 | 每次「立即掃描」完成後（不論 success / failed 都存） |
| 存檔位置 | `./reports/`（與 `nvr_scan.db` 同層） |
| 檔名格式 | `report_run<run_id>_<YYYYMMDD>_<HHMMSS>.pdf` |
| 內容 | 當下的未解決異常分組（與 `/abnormal/export.pdf` 同一份 PDF） |
| 保留策略 | **全部保留**（v1 簡化；一份 ~50KB 不占空間） |
| 觸發來源 | `web/app.py::_run_scan_in_background` 內呼叫 `web/report_archive.save_report` |

**與 `/abnormal/export.pdf` 的差異**：
- `/abnormal/export.pdf`：每次都即時生成，內容反映當下 DB；不下載就不留。
- `/reports/download/<run_id>`：下載**該次掃描完成時的歷史快照**，不受後續變動影響。

### 2.4 `/query` ad-hoc 頁安全性（Phase 1 Step 3b）

| 規則 | 行為 |
|---|---|
| 連線 | `PRAGMA query_only = ON`（SQLite 強制） |
| 白名單開頭 | 必須 `SELECT` / `WITH`（CTE）開頭 |
| 黑名單 keyword | `INSERT` / `UPDATE` / `DELETE` / `DROP` / `ALTER` / `CREATE` / `PRAGMA` / `ATTACH` / `REPLACE` / `VACUUM` / `REINDEX` / `LOAD` / `SAVEPOINT` / `BEGIN` / `COMMIT` / `ROLLBACK` / `ANALYZE` / `EXPLAIN` |
| 多 statement | 不允許中段分號（拒絕 `SELECT 1; SELECT 2`） |
| 註解處理 | `--` 單行、`/* */` 區塊會被 strip 後執行 |
| 回傳上限 | 500 筆（超過則標記 truncated badge） |
| 錯誤顯示 | `alert-danger` block，含拒絕原因

### 2.3 錯誤頁

| Status | 模板 | 用途 |
|---|---|---|
| 404 | `error.html` | run_id 找不到、路徑錯 |
| 500 | `error.html` | DB 不存在 / 內部錯誤 |

> 啟動時若 DB 檔不存在，會 `[WARN]` 印提示（網頁顯示空資料，不 crash）。

---

## 3. 整合測試 Mock API 端點

> 來源：`tests/integration/mock_acc.py`（2026-06-29 完成）。
> 整合測試用的 mock server，回應與真實 ACC 8.7 完全相容（同路徑、同包裝格式）。

| Mock 端點 | 行為 |
|---|---|
| `POST /mt/api/rest/v1/login` | 回 `{"status": "success", "result": {"session": "sess-<server_id>"}}` 或 403（`login_behavior="fail"`） |
| `GET /mt/api/rest/v1/server/ids` | 回 `[{"serverId": "<server_id>"}]` |
| `GET /mt/api/rest/v1/cameras` | 回 `{"cameras": [...]}`（動態注入） |
| `GET /mt/api/rest/v1/events/search` | 回 `{"events": [...]}`（動態注入） |

Mock server 啟動時自動生成自簽憑證（`openssl` CLI），scanner 端設 `verify_ssl=False` 對齊真實 NVR 場景。

---

## 4. 歷史變更

| 日期 | 版本 | 變更 | 原因 |
|---|---|---|---|
| 2026-06-18 | v1 | 初版：寫 `/login`、`/server/ids` 等 root level | 原始設計 |
| 2026-06-18 | v3 | 「混合命名空間」假設 | 文件對齊但實作錯 |
| 2026-06-23 | **v5（現行）** | **所有端點一律 `/mt/api/rest/v1/`** | ACC 8.7.3.4 實機驗證確認 |
| 2026-06-29 | v5.1 | 新增 v2 Web UI routes 段 | Web UI 雛形完成 |
| 2026-06-29 | v5.2 | 新增整合測試 Mock 端點段 | MockAvigilonServer 整合測試完成 |
| 2026-06-30 | v5.3 | `/events` 加 status 篩選 + `resolved_at` 視覺標記；新增 `/query` ad-hoc SELECT 頁 | Phase 1 Step 3a/3b |
| 2026-07-06 | v5.4 | 新增 §1.5 NVR Media API（port 8443）+ §1.5.4 Clip Web UI（port 8555）段 | Phase 2.7 影片片段調閱 |
| 2026-07-07 | v5.5 | 新增 §2.3 歷史 PDF 報告歸檔 + `/reports` + `/reports/download/<id>` 兩個 route；舊 `/abnormal/export.pdf` 改為「即時不存檔」 | 報告歸檔（user request） |
| 2026-09-21 | v5.6 | §1.5.4 Clip Web UI routes 標註 Week 6 #018 新住處（`web/blueprints_clips/*_bp.py`）；新增 `/clips/fetch_sync` `/clips/coverage` `/clips/coverage/data` 三條 | Week 6 Plan #018 執行完成 |
