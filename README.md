# Avigilon NVR 攝影機狀態掃描器 (Camera Status Scanner)

## 專案簡介

本專案透過 Avigilon Control Center (ACC) Web Endpoint API，連線至 NVR 伺服器，對系統內所有攝影機進行「當下異常狀態」總掃描。**不依賴逐台連線**，而是透過集中詢問 NVR 獲取即時的活躍警報（ACTIVE events）。

採用**架構二**（集中輪詢）與**per-NVR instance**（每台 NVR 一次 `AvigilonScanner` 物件、HTTP session 絕不跨 NVR 共用）。

## 雙 Flask App 架構

| Port | App | 啟動腳本 | 功能 |
|---|---|---|---|
| 8444 | `web/app.py` | `run_web.sh` / `.bat` / `.ps1` | Dashboard / Scan / 異常事件 / NVR CRUD / 查詢 / 報表 / /fleet / /wall |
| 8555 | `web/clips_app.py` | `run_clips.sh` / `.bat` / `.ps1` | 影片片段調閱（Phase 2.7）+ NVR CRUD + Dark Mode（**獨立可打包**） |

預設兩個 app 在 `127.0.0.1`：

```bash
# 8444 — 主 dashboard
PYTHONPATH=. python -m web.app 8444

# 8555 — clips app
PYTHONPATH=. python -m web.clips_app 8555
```

## 核心功能

- **自動登入與認證**：取得 Session Token 與 Server ID（SHA-256 auth）。
- **設備清單映射**：建立 `deviceId` 與攝影機名稱的對應表。
- **異常狀態過濾**：精準捕捉黑畫面 (TAMPER/BLIND)、斷線 (VIDEO_LOSS)、場景變更 (SCENE_CHANGE) 等事件。
- **多 NVR 批次掃描**：從 SQLite `nvr_servers` 表讀啟用清單，單台失敗不中斷整批。
- **影像健康檢查**（Phase 2.8）：frozen / blurry / overexposed / underexposed 偵測；frozen 修法 N 不觸發 event 但寫 metrics。
- **Web UI**：Fleet Pulse（/fleet 多 NVR 卡片）、雲端覆蓋圖、本機 gateway 健康 6 指標、cam 牆、異常事件、NVR CRUD、影片片段調閱。
- **Webhook 推播**：異常事件可推 Slack / Teams。
- **孤兒事件過濾**：dedup 後不會再顯示幽靈相機的舊事件。

## 開發環境

- Python 3.10+
- 依賴套件：
  - `requests>=2.28.0`（設 `verify=False` 忽略自簽證書）
  - `reportlab>=4.0`（PDF 報表）
  - `tzdata>=2024.1`（Windows 啟動 zoneinfo）
  - `psutil>=5.9`（Spec C 本機 gateway 健康）

```bash
# 建立虛擬環境
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS

# 安裝依賴
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 開發模式（含 pytest）
pip install -r requirements-web.txt  # Web UI（含 Flask）
```

## 認證輸入優先順序

1. Shell 環境變數（`AVIGILON_USER_KEY` / `AVIGILON_USER_NONCE`）
2. `.env` 檔（同目錄，不覆蓋 shell env）
3. 互動輸入（`getpass`）
4. `nvr_config.json`（fallback）

`.env` 與 `nvr_config.json` 都在 `.gitignore` 內，**絕不要 commit**。

## 開發指令

```bash
# 跑 unit test
pytest -q

# 跑單一測試
pytest tests/test_xxx.py::test_yyy -q

# 跑整合測試（真實 mock HTTPS）
pytest tests/integration/ -q

# 跑正式掃描（從 nvr_servers 讀所有 enabled NVR）
python -m batch_scan

# 打包成 Windows exe（功能穩定後）
.\build_web.bat   # 產 dist\web\web.exe
```

## 重要文件

- `overview.md` — v1/v2 架構定位、執行流程
- `api_endpoints.md` — 端點清單 + 混合命名空間陷阱
- `class_interface.md` — `AvigilonScanner` 介面契約（修改必同步）
- `database_schema.md` — SQLite Schema 契約（修改必同步）
- `CHANGELOG.md` — 變更歷史
- `CLAUDE.md` — Claude Code 操作指南
- `docs/clip-feature.md` — 影片片段調閱功能計畫
- `docs/media-api-research.md` — NVR Media API 規格

## 開發流程紀律

1. **每次修改 `.py` 後主動跑驗證**（`py_compile` + `pytest`）
2. **介面 / Schema 變更必同步契約文件**（`class_interface.md` / `database_schema.md`）
3. **完成 TODO 項目必勾掉** `todo_progress.md` 對應項目
4. **TDD 紀律**：先寫 failing test，再寫 code，再 commit
5. **簡潔優先**：不 speculative，不改善周邊程式碼

## 截圖

- 021.PNG — 原始 mockup（/fleet 藍圖）
- 026_task_a_after.png — Task A：ghost 過濾後
- 027_task_b_donut.png — Spec B 雲端覆蓋圖
- 028_spec_c_system_health.png — Spec C 本機 gateway 健康
- wall-real.png / wall-after-dedup.png — /wall 驗證
- fleet-real.png — /fleet 視覺驗證

## 授權

未商業化（詳見 `commercial_license_plan.md`）。
