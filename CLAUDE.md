# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Avigilon NVR 掃描器（Background Worker，v1）

## 專案現況（重要）

v1 範圍：多 NVR Background Worker，**只負責撈資料寫 SQLite**，不負責畫面。Web UI 為 v2，目前不實作。

| 階段 | 狀態 |
|---|---|
| 規劃文件（README / overview / api_endpoints / class_interface / database_schema） | ✅ |
| `nvr_config.json` 多 NVR 設定範本 | ✅ |
| `discover_event_subtopics.py`（步驟 0 探勘腳本） | ✅ |
| `AvigilonScanner` class（含 SHA-256 auth） | ✅ |
| `SqliteWriter`（`IDatabaseWriter` 實作） | ✅ |
| `batch_scan()` 多 NVR 入口 | ✅ |
| pytest 測試套件（97 unit + 35 整合 = 132 項） | ✅ |
| 部署指南 + 入口腳本（cron / 工作排程器） | ✅ |
| v2 Web UI 雛形（Flask 5 routes + 6 templates） | ✅ |
| 整合測試（MockAvigilonServer + MockWebhookReceiver） | ✅ |
| **Webhook 推播**（Slack / Teams 異常通知） | ✅ |
| GitHub Actions CI（`.github/workflows/ci.yml`，132 項測試自動跑） | ✅ |
| **Phase 1：事件 resolved 追蹤**（DB schema + worker `mark_resolved()` + Web UI /events status 篩選 + /query ad-hoc SELECT） | ✅ |
| **Phase 2.7：影片片段調閱**（MediaApiClient Protocol + MockClient + clips Flask app port 8555 + 兩段式 UX + 30 項測試） | ✅ |
| **Phase 2.7 補：NVR 連線失敗追蹤**（`nvr_failure_log` 表 + `log_nvr_failure()` + dashboard/runs_list 顏色化 + run_detail 明細） | ✅ |
| **Phase 2.7 補：DB-as-source-of-truth**（`nvr_servers.enabled` 欄位 + `list_enabled_nvrs()` + `set_nvr_enabled()` + Web toggle + batch_scan 讀 DB；`nvr_config.json` 降級為首次 init seed） | ✅ |

TODO 細項見 `todo_progress.md`。介面與 schema 變更必須同步更新契約文件（見下）。

## 必讀文件（按讀取順序）

1. `overview.md` — v1/v2 架構定位、執行流程
2. `api_endpoints.md` — 端點清單 + **混合命名空間陷阱**
3. `class_interface.md` — **`AvigilonScanner` 介面契約**（修改必同步更新）
4. `database_schema.md` — **SQLite Schema 契約**（修改必同步更新）

寫程式前先讀這四份；其餘文件（`deployment_backup.md`、`commercial_license_plan.md`、`wsdl.md`）為部署 / 未來規劃備忘。

**Phase 2.7 額外必讀**：
- `docs/clip-feature.md` — 影片片段調閱功能計畫（兩段式 UX）
- `docs/media-api-research.md` — NVR Media API 規格（從原廠 PDF 抽出）

## 核心架構要點

- **架構二**：向 NVR 集中輪詢 ACTIVE events，不一對一連線每台攝影機。
- **per-NVR instance**：每次掃一台 NVR 就 `AvigilonScanner(...)` 一個新物件，掃完銷毀，**HTTP session 絕不跨 NVR 共用**。
- **依賴注入**：`IDatabaseWriter` Protocol 抽象 DB 寫入層，測試可換 in-memory mock；`SqliteWriter` 是預設實作。
- **失敗不中斷整批**：單台 NVR 失敗僅記錄到 `scan_runs.status='partial'`，批次繼續。

## 步驟 0：事件字典探勘（撰寫 `scan()` 過濾邏輯前必做）

在實作 `AvigilonScanner.scan()` 的事件過濾**之前**，必須先跑 `discover_event_subtopics.py`：

```bash
python discover_event_subtopics.py
```

此腳本讀 `nvr_config.json` 第一台 NVR，登入後呼叫 `/mt/api/rest/v1/event-subtopics`，把 NVR 實際支援的事件主題字串印出來。

完成後：
1. 把實際回傳的「異常」主題字串回填到本檔下方「關鍵過濾關鍵字」段。
2. 同步更新 `class_interface.md` 的 `DEFAULT_ABNORMAL_KEYWORDS`。

若無可連線的 NVR，至少以 mock server 跑一次確認腳本能正常結束（見 `discover_event_subtopics.py` 的 `call_api()` 可注入 `requests.Session`）。

## 關鍵過濾關鍵字

僅以下事件主題視為「異常」（其他 ACTIVE 事件忽略）。ACC 8.7 實測主題（已從 `discover_event_subtopics.py` 探勘 229 個 DEVICE_* 確認）：

```python
ABNORMAL_KEYWORDS = (
    "DEVICE_VIDEO_SIGNAL_LOST",       # 影像訊號斷線
    "DEVICE_TAMPERING",               # 破壞 / 遮蔽
    "DEVICE_COMMUNICATION_LOST",      # 通訊中斷（防 events 沒生）
    "DEVICE_CONNECTION_ERROR",        # 連線錯誤
    "DEVICE_LONG_FAILED",             # 長期失敗（拔網路線會出現）
    "DEVICE_DISCONNECTED",            # 斷線
    "DEVICE_ANOMALY_START",           # 影像分析異常
    "DEVICE_UNUSUAL_STARTED",         # 未預期活動
)
```

**雙重檢查**：`scan()` 除了比對上述關鍵字，還會檢查 `cameras[].connectionStatus.state != "CONNECTED"`，確保 ACC 沒生事件時也能抓出離線相機。

## 已知陷阱

1. **混合命名空間**：root level 的 `/login` `/server/ids` `/cameras` `/events/search` 之外，`event-subtopics` **必須**帶 `/mt/api/rest/v1/` 前綴。是唯一例外的端點，記錯就 404。
2. **自簽 SSL**：NVR 多為自簽證書，`requests.Session.verify=False` 且必須 `urllib3.disable_warnings(InsecureRequestWarning)`。
3. **明碼密碼**：`nvr_config.json` 內 `username` / `password` 為明碼，建議加入 `.gitignore`；生產環境改用 secrets manager / env var 注入。
4. **`ConnectionError` 名稱衝突**：內建有同名例外，自訂例外不要直接叫 `ConnectionError`（`class_interface.md` 暫定 `ConnectionError_`，實作時再正名）。

## 開發指令

```bash
# 建立 / 啟用虛擬環境
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# 安裝依賴
pip install -r requirements.txt
# 開發模式（多 pytest）
pip install -r requirements-dev.txt

# 探勘事件字典（步驟 0）
python discover_event_subtopics.py

# 跑單元測試
pytest -q
pytest tests/test_xxx.py::test_yyy -q   # 跑單一測試

# 跑整合測試（真實 HTTPS mock server ↔ scanner ↔ DB ↔ Flask）
pytest tests/integration/ -q
# 整合測試預先 bundled 自簽憑證（tests/fixtures/cert.pem），不需 openssl
# pytest.ini 已自動過濾 InsecureRequestWarning

# 跑正式掃描（從 nvr_config.json 讀所有 enabled NVR）
python nvr_scanner.py

# 排程入口
./run_worker.sh           # Linux/macOS cron 用
run_worker.bat            # Windows 工作排程器用
powershell run_worker.ps1 # Windows PowerShell 工作排程器用

# v2 Web UI 啟動
pip install -r requirements-web.txt
./run_web.sh                       # Linux/macOS
.\run_web.ps1                      # Windows PowerShell（推薦，UTF-8 native；自動砍同 port 殘留 Flask）
run_web.bat                        # Windows cmd（legacy：中文 cmd 環境下有 BOM 編碼問題，盡量用 .ps1）
# 預設 http://127.0.0.1:8444（避開 NVR 8443；可由 NVR_WEB_PORT 環境變數覆寫）

# Phase 2.7 影片片段調閱 Web UI（給另一部門用，port 8555）
./run_clips.sh                     # Linux/macOS
.\run_clips.ps1                    # Windows PowerShell（自動砍同 port 殘留 Flask）
run_clips.bat                      # Windows cmd
# 預設 http://127.0.0.1:8555（避開 NVR 8443 / 既有 8444）
# NVR 端 Media API 仍在 port 8443（沿用 REST session token）
# NVR_CLIPS_CLIENT=mock 切到 MockMediaClient（不打真 NVR；測試用）
# 測試：pytest tests/test_clip_retrieval.py tests/test_clips_app.py -q

# 語法檢查（無需 import）
python -m py_compile path/to/file.py

# 打包成 Windows exe（功能穩定後執行，現階段不打包）
.\build_web.bat       # 只打包 web.exe
.\build_all.bat       # 同上（wrapper；呼叫 build_web.bat）
# 輸出：dist\web\web.exe（onedir 模式）
# 打包腳本全英文無中文（避免 .bat 編碼問題）；PyInstaller 6.x 會自動安裝
# 注意：worker.bat / worker.exe 已於 2026-07-03 移除 — 改用 Web UI「▶ 立即掃描」按鈕觸發
```

## `.bat` 檔的**唯一原始碼**：`_write_bat.py`

所有 `.bat` 檔（`build_web.bat` / `build_all.bat` / `run_web.bat` / `run_worker.bat` / `run_clips.bat`）都由 `_write_bat.py` 重新產生。
**絕對不要直接編輯 .bat 檔** — 改 Python 原始碼再跑 `_write_bat.py`。

```bash
# 修改任何 .bat 內容的正確流程：
python _write_bat.py        # 重新生成 4 個 .bat
git diff *.bat              # 確認差異符合意圖
pytest -q                   # 跑測試確認沒壞
```

為什麼需要這個工具 — Python 字串中 `\t` `\a` 等跳脫字元會在 `.bat` 內被解讀成 BEL/TAB 等不可見字元，
腐蝕 `%PD%web\templates` 這類路徑。`_write_bat.py` 全程使用 **bytes literal**，
`b"\\t"` 是兩個 ASCII 字元（反斜線 + t），沒有任何 `\X` 會被 Python 解讀。

> v1 TODO 已全部完成：AvigilonScanner / SqliteWriter / batch_scan / pytest / 部署指南 / 入口腳本。

## 認證輸入優先順序（由高到低）

1. Shell 環境變數（`export AVIGILON_USER_KEY=...`）
2. 同目錄 `.env` 檔（**不覆蓋** shell env；複製 `.env.example` 為 `.env` 再填）
3. 互動輸入（`userKey` / `password` 用 `getpass` 隱藏）
4. `nvr_config.json` 內 NVR 設定（僅 username / password）

支援的環境變數：

| 變數 | 必填 | 來源 fallback |
|---|---|---|
| `AVIGILON_USER_NONCE` | ✅ | 互動輸入 |
| `AVIGILON_USER_KEY` | ✅ | 互動輸入（getpass） |
| `AVIGILON_INTEGRATION_ID` | 選填 | 空字串 |
| `AVIGILON_USERNAME` | 選填覆寫 | `nvr_config.json` |
| `AVIGILON_PASSWORD` | 選填覆寫 | `nvr_config.json` |

`.env` 已在 `.gitignore` 內，**絕對不要 commit**。

## 開發流程紀律

每次建立或修改 `.py` 後，**主動跑驗證**才算完成：

1. **語法檢查**：`python -m py_compile <file>` 至少要過。
2. **單元測試**：`tests/` 建立後跑 `pytest -q`；新功能需附測試。
3. **乾跑 / 探測**：能 mock 就 mock；`AvigilonScanner` 的 `requests.Session` 已設計為可注入。
4. **結果摘要**：回覆中明確列出「驗證了什麼、輸出結果是什麼」。

介面 / Schema 變更紀律：
- 修改 `AvigilonScanner` 介面 → 同步更新 `class_interface.md`。
- 修改 DB schema → 同步更新 `database_schema.md`。
- 完成 TODO 項目 → 勾掉 `todo_progress.md` 對應項目（避免 context 漂移）。

排程部署（Linux Cron / Windows 工作排程器）見 `deployment_backup.md`。
入口腳本：`run_worker.sh`（Linux/macOS）、`run_worker.bat` / `run_worker.ps1`（Windows）。

v2 Web UI 啟動見 `run_web.sh` / `run_web.bat`，預設 `http://127.0.0.1:5000`。
Web 端為**唯讀**（v2 範圍），不與 background worker 競爭 DB 寫入。

## 多 NVR 設定檔

`nvr_config.json` 結構：
- `scan_settings`：DB 路徑（預設 `./nvr_scan.db`）、timeout、重試參數。
- `nvr_servers[]`：每台 NVR 一個物件，含 `id` / `name` / `host` / `port` / `username` / `password` / `verify_ssl` / `enabled`。

批次入口只掃 `enabled=true` 的項目；`enabled=false` 留著方便暫時停用單台。
