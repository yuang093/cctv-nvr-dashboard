# 待辦事項與進度 (TODO & Progress)

> 架構定位：v1 = Background Worker（多 NVR + SQLite）；Web UI 為 v2。
> 介面契約請見 `class_interface.md`，Schema 請見 `database_schema.md`。

---

## ✅ 已完成（規劃階段）

- [x] 建立專案基礎文件（README / overview / api_endpoints / database_schema / class_interface）
- [x] 確立架構二（向 NVR 集中查詢 ACTIVE events）
- [x] 設計 SQLite Schema（4 張表：nvr_servers / cameras / scan_runs / events）
- [x] 設計 `AvigilonScanner` 與 `IDatabaseWriter` 介面契約
- [x] 建立 `nvr_config.json` 多 NVR 設定範本
- [x] 規劃 Background Worker 定位與多 NVR 流程
- [x] 更新 `CLAUDE.md`：開發流程（驗證 + 步驟 0）、架構定位 v1

---

## 🔜 步驟 0：事件字典探勘

- [x] 實作 `discover_event_subtopics.py`（讀第一台 NVR，呼叫 event-subtopics）
- [x] Try-Except 覆蓋：Timeout / Auth (401/403) / ConnectionError / SSLError / JSONDecode
- [x] 終端機分類輸出（異常候選 vs 其他）
- [x] **實機測試**：經 `python discover_event_subtopics.py` 驗證 ACC 8.7.3.4 命名空間為 `/mt/api/rest/v1/`，非原規格 root level
- [x] **建立 `Avigilon_API_Reference.md`**：依原廠手冊整理 SHA-256 `authorizationToken` 規格（解決 `403 Unknown reason` 問題）
- [x] 把 NVR 實際回傳的關鍵字回填到 `CLAUDE.md`「關鍵過濾關鍵字」段落（DEVICE_* 8 條）

---

## 🏗 v1 核心實作

### 基礎建設
- [x] 建立 `.gitignore`（保護 `.env` / `nvr_config.json` / `__pycache__` / `*.db`）
- [x] 建立 `.env.example` 模板（複製為 `.env` 後填入金鑰）
- [x] `nvr_scanner.py` 支援 `.env` 檔載入（shell env 優先；零外部依賴）
- [x] 初始化 Python 專案：`requirements.txt` / `requirements-dev.txt`（`pyproject.toml` 為低優先 optional）
- [x] 建立虛擬環境建立 / 啟用說明文件（見 `deployment_backup.md §2.2`）

### DB 層（`db/sqlite_writer.py`）
- [x] 實作 `SqliteWriter`（實作 `IDatabaseWriter` Protocol）
- [x] 提供 schema 初始化（自動 CREATE TABLE IF NOT EXISTS）
- [x] 提供交易管理（單次掃描一個 transaction；finish commit、close rollback）
- [x] 撰寫單元測試（6 項 lifecycle / 查詢 / JOIN / 連續兩次 / 檔案持久化 / rollback / 錯誤處理 全 PASS）
- [x] `main()` 串接：upsert_nvr → begin_scan_run → scan → upsert_cameras → insert_events → finish_scan_run

### Scanner 層
- [x] **實作 `nvr_scanner.py`**（含 `AvigilonScanner` class，依 `class_interface.md` 契約）
  - [x] SHA-256 `authorizationToken` 計算（`compute_authorization_token()`）
  - [x] POST `/mt/api/rest/v1/login` 含 `clientVersion` 對齊 ACC 8.7.3.4
  - [x] 自訂例外類別：AuthError / ConnectionError_ / ApiResponseError / ScannerError
  - [x] 實作 `login() / get_server_ids() / get_cameras() / get_active_events() / scan()`
  - [x] Try-Except 覆蓋所有 requests 例外與 HTTP 狀態
  - [x] 終端機報表輸出（異常 / 正常設備清單）
  - [x] 認證輸入：環境變數優先，`getpass` 隱藏密碼
  - [x] 語法檢查 `py_compile` 通過
  - [x] **雙重異常檢查**：events 關鍵字 + `connectionStatus.state`
  - [x] **實機驗證完成**（2026-06-23）：登入、抓 cameras、偵測拔網路相機（LONG_FAILED state）
  - [x] **ABNORMAL_KEYWORDS 換成 ACC 8.7 實際 DEVICE_* 主題**
  - [x] 重寫 `discover_event_subtopics.py` 用新 auth（已可用）
  - [x] scan() 結果去重（event + state 同相機不重複列）
  - [x] 撰寫正式 pytest 測試套件（**58 項全綠**：`tests/test_*.py`）
  - [x] **DB 層實作完成**（`SqliteWriter` 已在 `db/sqlite_writer.py`，含 6 項 unit + 2 項 batch 整合測試）

### 批次入口（`batch_scan.py` 或 `main.py`）
- [x] 讀取 `nvr_config.json`，過濾 `enabled=True`
- [x] 對每台 NVR 建立 `AvigilonScanner` instance（per-NVR，不共用 session）
- [x] 收集結果並呼叫 `db_writer` 寫入
- [x] 失敗 NVR 不中斷整批，但記錄到 `scan_runs.status='partial'`
- [x] 終端機輸出本次掃描彙總（含失敗清單）
- [x] `nvr_scanner.py main()` 改為呼叫 `batch_scan()`（移除單機流程）
- [x] `SqliteWriter.finish_scan_run()` 擴充支援 `total_nvrs` / `ok_nvrs` / `failed_nvrs`

### 報表輸出（可選附加）
- [x] 終端機表格化輸出（總設備數 / 異常設備數 / 各 NVR 狀態）→ `_print_batch_summary()` + `print_report()`
- [x] 異常設備清單（攝影機名稱 + 事件主題）→ `print_report()` 內 `[異常事件]` 區段

---

## 🔧 部署與排程

- [x] 建立 `deployment_backup.md` 更新版（Background Worker 啟動方式）
- [x] 排程觸發：Linux Cron / Windows 工作排程器範例
- [x] 入口腳本：`run_worker.sh`（Linux/macOS cron）、`run_worker.bat` / `run_worker.ps1`（Windows 工作排程器）
- [x] 升級流程 / DB 遷移 / 定期備份腳本說明
- [x] 故障排除速查表（常見 8 種症狀 + 解法）
- [x] 錯誤通知（v1 不做 webhook）→ 透過 `run_worker.sh` exit code 與 log grep 達成

---

## 🧪 整合測試（end-to-end）

- [x] `tests/integration/mock_acc.py` — `MockAvigilonServer`：模擬 ACC 8.7 HTTPS API
  - 自簽憑證（openssl）+ SSLContext + threading HTTPS server
  - 支援動態注入 cameras / events / login_behavior
  - SO_REUSEADDR 避免 TIME_WAIT 阻塞多測試
  - TLS handshake 等 server 真的可服務才返回（修 flaky）
- [x] `tests/integration/conftest.py` — 共用 fixtures（multi_nvr_servers / integration_db / integration_config）
- [x] `tests/integration/test_e2e_single_nvr.py` — 8 項：scan() 全綠 / 雙重異常 / login-fail / state-only / DB 寫入
- [x] `tests/integration/test_e2e_batch_scan.py` — 8 項：partial / session 隔離 / DB transaction / login-fail 不污染 / 全成功 / 全失敗 / 空 nvr
- [x] `tests/integration/test_e2e_web.py` — 11 項：dashboard / runs_list / run_detail / nvrs_list / events_list / 篩選 / 404 / static CSS
- [x] 整合測試 27 項全綠；與 unit 69 項合計 **96 項 pytest 全綠**
- [x] 連跑 5 次驗證穩定（無 transient failure）

---

## 🔔 Webhook 推播（Slack / Teams）

- [x] `webhook.py` — 模組
  - `WebhookConfig` / `WebhookPayload` dataclass
  - `substitute_env_vars`：URL 內 `${ENV_VAR}` 環境變數替換
  - `_build_slack_payload`：Slack Incoming Webhook 格式（blocks + channel）
  - `_build_teams_payload`：MS Teams MessageCard 格式（themeColor + facts）
  - `send_webhook` / `send_webhooks`：失敗不丟例外，只回 (ok, error)
- [x] `nvr_config.json` 加 `webhooks` 段
- [x] `batch_scan.py` 整合：abnormal > 0 或 status='failed' 時觸發；結果加到 batch_result.webhook_results
- [x] `tests/test_webhook.py` — 28 項：env 替換 / Slack 格式 / Teams 格式 / 失敗處理 / batch 整合
- [x] `tests/integration/test_e2e_webhook.py` — 8 項：mock receiver 真實 POST / 觸發條件 / 失敗不影響 batch
- [x] `tests/integration/mock_acc.py` 加 `MockWebhookReceiver`（自動收集收到的 payload）
- [x] `deployment_backup.md §8` — 完整的取得 webhook URL + 設定指南 + 故障排除
- [x] pytest 96 → **132 項全綠**（連跑 3 次穩定）

---

## 📄 文件同步（整合測試後補）

- [x] `overview.md` — v2 標記完成、加整合測試段、修舊版 flow（save_to_db 未實作）
- [x] `api_endpoints.md` — **修正「混合命名空間」錯誤**（實測全 `/mt/api/rest/v1/`）+ 加 web routes 段 + 加 Mock 端點段
- [x] `class_interface.md` — 加 MockAvigilonServer/Config interface + 測試分布表
- [x] `deployment_backup.md` — 加 §3.4 CI、§3.5 v2 Web UI、§7 整合測試執行段

---

## 🎁 v1 完成總結

| 範疇 | 狀態 |
|---|---|
| AvigilonScanner（含 SHA-256 auth + 雙重異常檢查） | ✅ |
| SqliteWriter（含 transaction / batch stats） | ✅ |
| batch_scan()（多 NVR 協調 + partial/failed 狀態） | ✅ |
| pytest unit 套件 | ✅ 69 項全綠 |
| pytest integration 套件 | ✅ 27 項全綠（mock HTTPS server 真實傳輸） |
| 部署指南 + 入口腳本 | ✅ |
| 規劃文件 / 契約文件 | ✅ |

**v1 全部核心 TODO 完成** 🎉。剩餘為 v2 範圍或低優先 optional。

---

## 🚫 v2（不在 v1 範圍，已啟動 v2 雛形）

- [x] Web UI 雛形（Flask 5 routes + 6 templates + 11 項 smoke test）
  - 路由：`/` `/runs` `/runs/<id>` `/nvrs` `/events`
  - 唯讀設計（不與 worker 競爭 DB 寫入）
  - 啟動入口：`run_web.sh` / `run_web.bat`
- [x] Webhook 推播（Slack / Teams，§8 已完整實作 + 36 項測試）
- [x] GitHub Actions CI（`.github/workflows/ci.yml`，3×Python 矩陣 + pip cache + 132 項測試）
- [x] **Phase 1 事件 resolved 追蹤**（2026-06-30）：DB schema 加 `resolved_at` + worker `mark_resolved()` + Web UI `/events` status 篩選 + `/query` ad-hoc 頁
- [x] **Spec G Cam 健康趨勢圖**（2026-08-06）：`/trends` 頁面（24h/7d mini sparkline + inline detail）+ 4 處 deep-link（navbar / devices_list / dashboard top_missing / coverage 8555 cross-port via `NVR_DASHBOARD_URL` env）+ `?cam_id=` auto-expand。25 commits（plan + 4 batches）、974 tests 全綠、5 張視覺驗證截圖。零 schema 改動。Spec：`docs/superpowers/specs/2026-08-05-cam-health-trends-design.md`。
- [ ] 常駐服務（systemd / NSSM）
- [ ] 帳號權限管理（`users` / `roles`）
- [ ] 即時更新（WebSocket / SSE）

---

## 變更紀律
- 每完成一項，**立即更新本檔案**（避免 context 漂移）。
- 介面或 schema 變更必須同步更新 `class_interface.md` 與 `database_schema.md`。
- 完成後主動跑驗證（語法 / 單元測試 / 乾跑），不可省略（見 `CLAUDE.md`「開發流程」）。