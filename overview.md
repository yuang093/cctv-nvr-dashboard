# 系統架構總覽 (Overview)

## 架構選擇：架構二 (NVR 集中狀態獲取)
本系統採動向核心 NVR/ACC 伺服器集中輪詢的架構。相較於一對一連線數十台攝影機，此架構能最大化執行效率，適合「執行程式、掃描全部、產出報表」的批次任務需求。

## 架構演進：單機 CLI → 多 NVR 背景服務 → 加 Web UI

| 版本 | 範圍 | 狀態 |
|---|---|---|
| **v1（核心完成）** | 多 NVR 背景服務（Worker） | ✅ 從 `nvr_config.json` 讀取 NVR 清單，批次掃描後寫入 SQLite |
| **v2 雛形（完成）** | Web UI 報表呈現層 | ✅ 由獨立的 Flask 服務讀取 SQLite 渲染畫面；與 worker 解耦 |

> **定位**：本掃描程式（`nvr_scanner.py` / `batch_scan.py`）**只負責「撈取與寫入資料」**。畫面由 v2 Web UI 提供（可獨立部署）。

## v1 核心架構流程

1. **初始化**：載入 `nvr_config.json`，建立 `SqliteWriter` 連線。
2. **會話建立（每台 NVR 各做一次）**：呼叫 `/mt/api/rest/v1/login` 獲取 Session，呼叫 `/mt/api/rest/v1/server/ids` 取得 Server ID。
3. **設備對應（每台 NVR）**：呼叫 `/mt/api/rest/v1/cameras` 獲取該 NVR 下所有攝影機（含 `connectionStatus.state`）。
4. **狀態快照（每台 NVR）**：呼叫 `/mt/api/rest/v1/events/search?queryType=ACTIVE` 獲取當下未解除之異常事件。
5. **邏輯比對（雙重異常檢查）**：
   - 異常來源 1：ACTIVE 事件主題匹配 `ABNORMAL_KEYWORDS`
   - 異常來源 2：相機 `connectionStatus.state != "CONNECTED"`（防 ACC 沒生事件）
   - 去重：同一相機同一來源不重複列
6. **持久化**：呼叫 `SqliteWriter.upsert_cameras()` + `insert_events()`。
7. **批次彙總**：寫入 `scan_runs` 表（含 `status: success/partial/failed`、總 NVR/相機/異常數）。
8. **報表**：可選地於終端機輸出本次掃描彙總。

詳細 API 路徑與契約見 `api_endpoints.md` / `Avigilon_API_Reference.md`。

## v2 Web UI（已完成雛形）

- **技術**：Flask 3.0 + Bootstrap 5（CDN）+ Jinja2 templates。
- **路由**：5 個頁面（`/`、`/runs`、`/runs/<id>`、`/nvrs`、`/events`），加 error handler（404/500）。
- **連線**：與 worker 解耦，自開 SQLite 連線讀取（`PRAGMA query_only = ON` 避免意外寫入）。
- **啟動**：`./run_web.sh`（Linux/macOS）/ `run_web.bat`（Windows）；預設 `http://127.0.0.1:5000`。
- **端點規格**：見 `api_endpoints.md §2`。

## 整合測試（已完成）

- **目標**：在沒真實 NVR 的開發機上跑完整 pipeline（mock HTTPS server ↔ scanner ↔ DB ↔ Flask）。
- **覆蓋**：27 項整合測試（單 NVR 8 + batch 8 + Web 11），與 69 項 unit 合計 **96 項 pytest 全綠**。
- **Mock 設計**：`MockAvigilonServer`（`tests/integration/mock_acc.py`）用 `openssl` 生成自簽憑證 + Python `ssl` 模組 + threading HTTP server 模擬 ACC 8.7 API。
- **執行**：`pytest tests/integration/ -q`（已整合進 CI-ready）。
- **設計重點**：
  - 真實 HTTPS 傳輸（不是 mock `requests.Session`）
  - 真實 SQLite 跨連線（worker writer + web reader）
  - 真實 Flask 渲染（Flask test client + template）

詳細介面見 `class_interface.md §測試架構`、部署見 `deployment_backup.md §整合測試`。

## 多 NVR 支援設計原則

- `AvigilonScanner` 採 **per-NVR instance** 模型：每次掃一台 NVR 就建立一個 instance，掃完銷毀，避免 session 互相污染。
- 批次入口（`batch_scan(config, credentials, writer)`）負責迴圈所有 NVR、收集結果、統一寫入 DB。
- NVR 之間的異常判定邏輯共用，但不共享 HTTP session。

## 執行模式

- **手動觸發**：`python nvr_scanner.py`（會自動呼叫 `batch_scan()`）
- **排程觸發**：Linux Cron / Windows 工作排程器（見 `deployment_backup.md`）
- **Web UI 常駐**：`./run_web.sh` 或 `run_web.bat`（不建議放 cron，Web 是常駐服務；systemd/NSSM 包裝見 v2 規劃）

## 與原始設計的差異

- ✅ **保留**：架構二集中輪詢、4 個 API 端點流程、ACTIVE events 過濾。
- 🆕 **新增**：多 NVR 設定檔、SQLite 持久化、Background Worker 定位、SHA-256 認證、雙重異常檢查、Web UI 雛形、整合測試。
- 🚫 **不做（v1 範圍）**：Webhook 推播、即時 WebSocket、攝影機一對一連線、帳號權限管理。

## v2 規劃（已部分完成，待擴充）

- [x] Web UI 雛形（Flask 5 routes + 6 templates）
- [x] 整合測試（MockAvigilonServer + 真實 SQLite）
- [ ] Webhook 推播（Slack / Teams 異常通知）
- [ ] 事件 resolved 追蹤（`events.resolved_at`）
- [ ] 即時更新（WebSocket / SSE）
- [ ] 常駐服務（systemd / NSSM）
- [ ] 帳號權限管理（users / roles）
- [ ] CI（GitHub Actions 跑 pytest）
