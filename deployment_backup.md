# 部署與備份指南 (Deployment & Backup)

> v1 = Background Worker，**無常駐 process、無 daemon**。每次掃描由 OS 排程器觸發。
> v2 雛形 = Web UI（Flask），見 §3.5。常駐服務（systemd / NSSM）為 v2 後續範圍。
> 建議每 5-15 分鐘掃一次（ACTIVE 事件通常在 NVR 端就累積了，不需要即時）。

---

## 1. 環境需求

| 項目 | 需求 |
|---|---|
| Python | 3.10+（測試於 3.11.9） |
| 磁碟 | ~50 MB（程式碼 + venv + DB）；DB 成長極慢（每 15 分鐘掃一次，一個月約 1-5 MB） |
| 網路 | 可直連每台 NVR 的 HTTPS 8443（防火牆需放行） |
| 權限 | 一般使用者即可；不要用 root / Administrator 跑 |
| 帳號 | NVR 上的 `api_reader` 角色或同等讀取權限即可（**不要**用 administrator） |

---

## 2. 部署步驟（一次性）

### 2.1 取得程式碼

```bash
git clone <repo-url> /opt/nvr        # Linux/Mac 範例路徑
# 或
git clone <repo-url> C:\nvr          # Windows
cd /opt/nvr                          # 進入專案目錄
```

### 2.2 建立虛擬環境

```bash
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows (cmd):
venv\Scripts\activate
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

### 2.3 安裝依賴

```bash
pip install -r requirements.txt
# 開發模式額外（要跑測試才需要）：
pip install -r requirements-dev.txt
```

### 2.4 設定 Avigilon 開發者金鑰

```bash
cp .env.example .env
# 用編輯器開 .env，填入：
#   AVIGILON_USER_NONCE=<向 Avigilon 申請的 userNonce>
#   AVIGILON_USER_KEY=<向 Avigilon 申請的 userKey>
#   AVIGILON_INTEGRATION_ID=<可選>
#   AVIGILON_USERNAME=<可選，覆寫 nvr_config.json 內所有 NVR 帳號>
#   AVIGILON_PASSWORD=<可選，覆寫 nvr_config.json 內所有 NVR 密碼>
```

> ⚠️ `.env` **已在 `.gitignore` 內，絕對不要 commit**。

### 2.5 設定 NVR 清單

編輯 `nvr_config.json`：

```json
{
  "scan_settings": {
    "db_path": "./nvr_scan.db",
    "timeout_seconds": 10,
    "retry_per_nvr": 2,
    "retry_backoff_seconds": 3,
    "log_level": "INFO"
  },
  "nvr_servers": [
    {
      "id": "branch-a",
      "name": "A 分店 NVR",
      "host": "192.168.1.100",
      "port": 8443,
      "username": "api_reader",
      "password": "REPLACE_ME",
      "verify_ssl": false,
      "site_id": null,
      "tags": ["branch", "taipei"],
      "enabled": true
    }
  ]
}
```

> ⚠️ `nvr_config.json` **含明碼密碼**，已在 `.gitignore` 內。**生產環境改用 secrets manager 注入**或把 `password` 欄位留空、用 `AVIGILON_PASSWORD` 環境變數覆寫。

### 2.6 首次手動驗證

```bash
python nvr_scanner.py
```

預期輸出（成功）：
```
[INFO] 已從 .env 載入 5 個環境變數
[INFO] DB 路徑：./nvr_scan.db
[INFO] 目標 NVR 數：3
[INFO] batch scan_run_id = 1（共 3 台 NVR）
...
[OK] 目前無 ACTIVE 異常事件
```

執行後檢查 DB：
```bash
sqlite3 ./nvr_scan.db "SELECT id, status, total_cameras, abnormal_cameras FROM scan_runs ORDER BY id DESC LIMIT 5"
```

---

## 3. 排程執行

### 3.1 Linux / macOS — Cron

**入口腳本** `run_worker.sh`：

```bash
#!/usr/bin/env bash
# 設定專案目錄
PROJECT_DIR="/opt/nvr"
VENV_PY="$PROJECT_DIR/venv/bin/python"
LOG_FILE="/var/log/nvr_scanner.log"

cd "$PROJECT_DIR" || exit 1
# venv 內 python 已自帶 .env 載入（透過 nvr_scanner.py），無需 source activate

$VENV_PY nvr_scanner.py >> "$LOG_FILE" 2>&1
```

`chmod +x run_worker.sh` 後加入 crontab：

```bash
crontab -e
# 編輯加上：
*/15 * * * * /opt/nvr/run_worker.sh
```

> 每 15 分鐘跑一次。視 NVR 數量與網路狀況可調成 `*/5`（5 分鐘）或 `*/30`（30 分鐘）。
> 建議加 `MAILTO=""` 避免每次成功都寄信。

**logrotate**（避免 log 檔無限長）：
```bash
# /etc/logrotate.d/nvr_scanner
/var/log/nvr_scanner.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

### 3.2 Windows — 工作排程器

**入口腳本** `run_worker.bat`：
```bat
@echo off
cd /d C:\nvr
call venv\Scripts\activate.bat
python nvr_scanner.py >> logs\nvr_scanner.log 2>&1
```

**設定步驟**：
1. 開啟「**工作排程器**」（`taskschd.msc`）
2. 右側「**建立工作**」（不要用「建立基本工作」）
3. 「**一般**」分頁：
   - 名稱：`NVR Scanner Worker`
   - 不論使用者登入與否均執行 ✓
   - 以最高權限執行（v1 不需要，但保險起見）
4. 「**觸發程序**」分頁 → 新增：
   - 每日
   - 開始時間：`00:00:00`
   - 重複工作間隔：`15 分鐘`，持續時間：`1 天` ✓
5. 「**動作**」分頁 → 新增：
   - 程式/指令碼：`C:\nvr\run_worker.bat`
6. 「**設定**」分頁：
   - 允許按需求執行工作 ✓
   - 若工作失敗，每 1 分鐘重新啟動，最多 3 次
7. 確定 → 輸入 Windows 帳號密碼

**PowerShell 版** `run_worker.ps1`（給 PowerShell 工作排程用）：
```powershell
$ErrorActionPreference = "Continue"
Set-Location "C:\nvr"
& ".\venv\Scripts\Activate.ps1"
python nvr_scanner.py 2>&1 | Tee-Object -FilePath "logs\nvr_scanner.log" -Append
```

### 3.3 容器化（可選）

未提供 Dockerfile。**v1 設計假設直接跑在主機上**（用 OS 排程器）。若要容器化，建議：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nvr_scanner.py batch_scan.py ./
COPY db/ ./db/
CMD ["python", "nvr_scanner.py"]
```

搭配 `docker run --env-file .env -v nvr-data:/app nvr-scanner` 與 host 的 cron 觸發。

### 3.4 CI 環境整合（GitHub Actions）

**已建立 `.github/workflows/ci.yml`**，push / PR / 手動觸發都會跑全套 132 項測試。

```yaml
# 觸發條件
on:
  push:        branches: [master, main]
  pull_request: branches: [master, main]
  workflow_dispatch:

# 矩陣：Python 3.10 / 3.11 / 3.12
strategy.matrix.python-version: ["3.10", "3.11", "3.12"]

# steps
- actions/checkout@v4
- actions/setup-python@v5（cache: pip）
- pip install -r requirements-dev.txt（含 flask）
- ls -l tests/fixtures/cert.pem key.pem  # 預先包好的憑證
- pytest -q
```

> **不需要 openssl CLI**：CI 用預先包好的自簽憑證（見 §7）。

#### 本地重現 CI 流程

```bash
# 乾淨環境（replicate CI）
python -m venv ci-env
source ci-env/bin/activate        # Linux/macOS
# ci-env\Scripts\activate          # Windows
pip install -r requirements-dev.txt
pytest -q
```

預期：`132 passed in ~28s`。

### 3.5 v2 Web UI（Flask，常駐服務）

> 入口腳本：`./run_web.sh`（Linux/macOS）/ `run_web.bat`（Windows）。
> **不建議放 cron**（cron 適合週期任務；Web 是常駐服務）。

#### 安裝

```bash
pip install -r requirements-web.txt
```

#### 啟動

```bash
# 預設 127.0.0.1:5000、DB 用 ./nvr_scan.db
./run_web.sh

# 自訂（環境變數）
NVR_WEB_HOST=0.0.0.0 NVR_WEB_PORT=8080 NVR_DB_PATH=/opt/nvr/nvr_scan.db ./run_web.sh
```

> ⚠️ 對外暴露時務必加反向代理（Nginx / Apache）+ auth（HTTP Basic / OAuth）。

#### 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `NVR_WEB_HOST` | `127.0.0.1` | 綁定 IP；**不要用 0.0.0.0** 除非有反向代理 |
| `NVR_WEB_PORT` | `5000` | 埠號 |
| `NVR_DB_PATH` | `./nvr_scan.db` | SQLite 檔案路徑 |
| `NVR_WEB_DEBUG` | `false` | Flask 除錯模式 |

#### 生產部署（v2 待完成）

- **systemd unit**（Linux）：見 `commercial_license_plan.md`（v2 後續）
- **NSSM 包裝**（Windows）：見 v2 後續

#### 手動驗證

```bash
curl -s http://127.0.0.1:5000/ | grep -q "NVR" && echo "OK"
curl -s http://127.0.0.1:5000/runs -o /dev/null -w "%{http_code}\n"  # 200
```

#### Routes 清單（Phase 1 起）

| 路徑 | 用途 | 新增時段 |
|---|---|---|
| `/` | Dashboard | v2 雛形 |
| `/runs` | 掃描紀錄分頁 | v2 雛形 |
| `/runs/<id>` | 單次掃描詳情 | v2 雛形 |
| `/nvrs` | NVR 清單 | v2 雛形 |
| `/events` | 異常事件（含 status=open/resolved/all 篩選 + resolved 視覺標記） | **Phase 1 Step 3a** |
| `/query` | Ad-hoc 唯讀 SELECT（白名單 + 黑名單 + 500 筆上限） | **Phase 1 Step 3b** |

> `/query` 是 read-only 的 SQL playground，給操作人員手動查 DB 狀態，但因允許任意 SELECT，建議只開在內網或加 auth（v2 後續）。

#### /query 安全機制（Step 3b 詳述）

| 層 | 防護 |
|---|---|
| SQLite | `PRAGMA query_only = ON`（即使 SQL injection 也寫不了） |
| 白名單 | 只允許 `SELECT` / `WITH ... SELECT` |
| 黑名單 | INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA 等 19 個 keyword |
| 多 statement | 拒絕中段分號 |
| 筆數 | 最多 500，超過顯示「截斷」badge |
| 錯誤 | `alert-danger` block，給操作員明確拒絕原因 |

---

## 4. 升級與維護

### 4.1 升級流程

```bash
cd /opt/nvr
# 1. 備份
cp nvr_scan.db nvr_scan.db.bak.$(date +%Y%m%d)

# 2. 拉新版本
git pull

# 3. 更新依賴（若有新增）
source venv/bin/activate
pip install -r requirements.txt

# 4. 跑測試（避免升級破壞既有功能）
pytest -q

# 5. 驗證：手動跑一次
python nvr_scanner.py
```

### 4.2 DB 遷移

v1 還沒有正式 migration 機制。`SqliteWriter._init_schema()` 使用 `CREATE TABLE IF NOT EXISTS`，所以：
- **新增欄位** → 用 `ALTER TABLE` 寫一次性腳本
- **新增表** → 放到 `SCHEMA_SQL` 內自動建
- **破壞性變更**（改欄位型別/名稱）→ 備份 → 砍 DB 重來

### 4.3 定期備份

```bash
# 每日備份 DB（保留 30 天）
0 2 * * * cp /opt/nvr/nvr_scan.db /backup/nvr_scan.db.$(date +\%Y\%m\%d)
0 3 * * * find /backup -name "nvr_scan.db.*" -mtime +30 -delete
```

---

## 5. 監控與日誌

### 5.1 日誌位置

| OS | 預設路徑 |
|---|---|
| Linux | `/var/log/nvr_scanner.log`（見 `run_worker.sh`） |
| Windows | `C:\nvr\logs\nvr_scanner.log` |

### 5.2 簡單監控（v1 不做 webhook）

監控 NVR 掃描失敗：

```bash
# 最後一行若出現 [FAIL] 或 status=failed 應通知
tail -n 50 /var/log/nvr_scanner.log | grep -q "\[FAIL\]" && \
  echo "NVR Scanner 出現失敗，請查 log" | mail -s "NVR Alert" admin@example.com
```

監控 DB 異常事件：
```bash
# 最近一小時有異常事件 → 通知
sqlite3 /opt/nvr/nvr_scan.db \
  "SELECT COUNT(*) FROM events WHERE detected_at > datetime('now', '-1 hour')" | \
  awk '{if ($1 > 0) print "異常事件 " $1 " 件"}' | \
  mail -s "NVR Abnormality" admin@example.com
```

### 5.3 v2 規劃（部分完成，待擴充）

- [x] Web UI 雛形（Flask 5 routes；見 §3.5）
- [x] Webhook 推播（Slack / Teams；見 §8）
- [ ] 常駐服務（systemd / NSSM）+ WebSocket 推播

---

## 6. 故障排除

| 症狀 | 可能原因 | 解法 |
|---|---|---|
| `ConnectionError_: 連線逾時` | 防火牆擋住 / NVR 離線 | `telnet <host> 8443` 確認可達 |
| `SSL 錯誤`（已 `verify_ssl: false` 仍報） | requests 套件版本過舊 | `pip install -U requests` |
| `403 Unknown reason`（登入） | `AVIGILON_USER_KEY` 沒設或設錯 | 確認 `.env` 內 `USER_KEY` 正確 |
| `403 Unknown reason`（已設） | `clientVersion` 與 NVR 不符 | 透過 `discover_event_subtopics.py` 取實際版本，覆寫 `DEFAULT_CLIENT_VERSION` |
| 抓不到 cameras（count=1） | 沒帶 `pageSize` | 確認 `nvr_scanner.py` 內有 `pageSize=100`（預設值，理論上不會發生） |
| DB 出現 `no such table` | 連線換過、`:memory:` 模式共用失敗 | 確認走檔案 DB（不要用 `:memory:` 在多 instance 場景） |
| 攝影機斷線未偵測 | events 沒生，但 `connectionStatus.state` 應有 | 確認 `ABNORMAL_KEYWORDS` 含 `DEVICE_LONG_FAILED` 等，並檢查 `scan()` 雙重檢查邏輯 |
| Cron 沒跑 | 路徑錯誤 / venv 沒啟動 | 用 `which python` 確認 venv 路徑，cron 內必須給絕對路徑 |

**debug 工具**：
```bash
# 重新探勘事件字典（看 NVR 實際支援哪些主題）
python discover_event_subtopics.py

# 跑完整測試（回歸確認）
pytest -q
```

---

## 7. 整合測試執行（end-to-end）

> 27 項整合測試：mock HTTPS server ↔ AvigilonScanner ↔ SqliteWriter ↔ Flask。
> 在沒真實 NVR 的開發機上驗證完整 pipeline，保護未來重構不退化。

### 7.1 環境需求

- Python 3.10+（同 §1）
- Python `ssl` 模組（標準庫，內建）
- ~~`openssl` CLI~~ — **不需要**！預先 bundled 自簽憑證（`tests/fixtures/cert.pem` + `key.pem`）已包進版控；只有刪除 fixtures 時才 fallback 到 openssl 重新生成
- 整合測試用 Python `threading`（標準庫）跑 mock server，不需額外依賴

### 7.2 執行指令

```bash
# 全部測試（unit + integration，96 項，約 22 秒）
pip install -r requirements-dev.txt
pytest -q

# 只跑整合測試（27 項，約 11 秒）
pytest tests/integration/ -q

# 只跑某個場景
pytest tests/integration/test_e2e_batch_scan.py -v
pytest tests/integration/test_e2e_web.py::TestWebEndToEnd::test_dashboard_shows_real_stats -v
```

### 7.3 整合測試覆蓋

| 檔案 | 項數 | 內容 |
|---|---|---|
| `test_e2e_single_nvr.py` | 8 | 單 NVR scan() + DB 寫入 + 雙重異常檢查 + login-fail |
| `test_e2e_batch_scan.py` | 8 | 3 NVR 批次 + partial/failed 狀態 + 真實 SQLite 交易 |
| `test_e2e_web.py` | 11 | Flask 渲染 + 真實跨連線讀寫 + 篩選 |
| **小計** | **27** | — |

### 7.4 Mock 設計

`MockAvigilonServer`（`tests/integration/mock_acc.py`）：
- 優先使用 `tests/fixtures/cert.pem` + `key.pem` 的預先包憑證（10 年效期），不需外部 openssl
- 只有 fixtures 缺失才 fallback 到 `openssl req -x509` 動態生成
- Python `ssl.SSLContext` + HTTPServer（threading `serve_forever`）
- 動態注入 cameras / events / login_behavior（`ok` / `fail`）
- 啟動時等 TLS handshake 完成才返回（避免 race）
- `SO_REUSEADDR` 避免 TIME_WAIT 阻塞多測試

### 7.5 常見疑問

| 症狀 | 原因 | 解法 |
|---|---|---|
| `FileNotFoundError: openssl` | fixtures 缺失且系統無 openssl | 確認 `tests/fixtures/cert.pem` + `key.pem` 存在；或裝 openssl |
| 整合測試第一次跑時連線被拒 | mock server 啟動 race | 已用 TLS handshake 等待緩解，仍遇到就把 `mock_nvr_server.start()` 加 sleep |
| 大量 `InsecureRequestWarning` | pytest.ini filter 失效 | 用 `-W error::InsecureRequestWarning` 移除；或直接 `urllib3.disable_warnings()` |

### 7.6 GitHub Actions CI（已啟用）

**檔案位置**：`.github/workflows/ci.yml`（Phase 0 #1 已完成，2026-06-30）。

#### 觸發條件

| 事件 | 觸發 |
|---|---|
| `push` 到 `master` / `main` | ✅ |
| `pull_request` 到 `master` / `main` | ✅ |
| 手動從 Actions 頁面觸發 | ✅ |

#### 矩陣策略

- **OS**：`ubuntu-latest`（內建 openssl + Python 3.10+ 預裝）
- **Python 版本**：`3.10` / `3.11` / `3.12`（用 `fail-fast: false`，單一版本失敗不擋其他）
- **cache**：用 `actions/setup-python@v5` 的 `cache: pip`（基於 requirements*.txt hash）

#### 不依賴外部 CLI

- 整合測試用 `tests/fixtures/cert.pem` + `key.pem`（預先包好的 10 年 self-signed），不需要 openssl CLI
- 也支援 fallback 動態呼叫 openssl（已存在但 CI 路徑用不到）

#### 本地重現

```bash
pip install -r requirements-dev.txt  # 含 flask
pytest -q                            # 132 passed in ~28s
```

#### 首次啟用步驟（GitHub 端）

1. 把程式碼 push 到 GitHub（local repo 還沒綁 remote）
2. 預設會自動跑第一次 CI
3. 之後 PR / push 都會自動跑

> 任何 push 失敗都會在 PR 上顯示 ❌，可擋 merge。

---

## 8. 資料與環境備份清單

升級前 / 改 schema 前必備份：

- [ ] `nvr_scan.db`（資料本體）
- [ ] `.env`（認證）
- [ ] `nvr_config.json`（NVR 清單 + 帳密）
- [ ] `venv/`（虛擬環境，可用 `pip freeze > requirements.lock.txt` 重現）
- [ ] 整個 `tests/` 目錄（重現測試狀態）

還原順序：clone → venv → pip → .env → nvr_config.json → nvr_scan.db

---

## 8. Webhook 推播設定（Slack / Teams）

> 來源：`webhook.py`（v2 已實作）。Worker 結束掃描時若偵測到 `abnormal_cameras > 0`（或 `status == "failed"`）自動 POST 到設定的 webhook URL。

### 8.1 觸發條件

| 條件 | 是否觸發 |
|---|---|
| `abnormal_cameras > 0` | ✅ 觸發 |
| `abnormal_cameras == 0` 且 `status == "success"` | ❌ 不觸發 |
| `status == "failed"`（全部 NVR 連不上） | ✅ 觸發 |
| webhook 接收端回非 2xx / timeout | 不中斷 worker；只記 log + 結果註記在 `batch_result.webhook_results` |

### 8.2 取得 Webhook URL

#### Slack

1. https://api.slack.com/apps → 選 / 建 App
2. **Incoming Webhooks** → 啟用
3. **Add New Webhook to Workspace** → 選 channel
4. 複製 Webhook URL（格式 `https://hooks.slack.com/services/T.../B.../...`）

#### Microsoft Teams

1. Team channel → `⋯` → **Connectors**（連接器）
2. **Incoming Webhook** → Configure → 命名 → Create
3. 複製 URL（格式 `https://outlook.office.com/webhook/...`）

### 8.3 設定 `nvr_config.json`

在 `scan_settings` 與 `nvr_servers` 之間加 `webhooks` 段：

```json
{
  "scan_settings": { "db_path": "./nvr_scan.db", ... },
  "webhooks": [
    {
      "provider": "slack",
      "url": "${NVR_WEBHOOK_SLACK_URL}",
      "channel": "#nvr-alerts",
      "enabled": true
    },
    {
      "provider": "teams",
      "url": "${NVR_WEBHOOK_TEAMS_URL}",
      "enabled": true
    }
  ],
  "nvr_servers": [...]
}
```

URL 用 `${ENV_VAR}` 語法從環境變數注入，避免寫明碼進 git。

### 8.4 設定環境變數

#### Shell（Linux / macOS）

```bash
export NVR_WEBHOOK_SLACK_URL="https://hooks.slack.com/services/T000/B000/XXXX"
export NVR_WEBHOOK_TEAMS_URL="https://outlook.office.com/webhook/XXXX/IncomingWebhook/..."
```

#### `.env` 檔

```bash
# .env（記得加入 .gitignore）
NVR_WEBHOOK_SLACK_URL=https://hooks.slack.com/services/T000/B000/XXXX
NVR_WEBHOOK_TEAMS_URL=https://outlook.office.com/webhook/XXXX/...
```

#### Windows（PowerShell）

```powershell
$env:NVR_WEBHOOK_SLACK_URL = "https://hooks.slack.com/services/..."
$env:NVR_WEBHOOK_TEAMS_URL = "https://outlook.office.com/webhook/..."
```

### 8.5 訊息格式範例

**Slack**：

> ⚠️ **NVR 掃描：partial**
> 異常相機：3 / 8
> NVR 成功/失敗：2 / 1（總 3）
> 耗時：5.0s
>
> 前 5 大異常：
>   • `cam-001`: DEVICE_VIDEO_SIGNAL_LOST
>   • `cam-002`: DEVICE_TAMPERING
>   • `cam-003`: STATE_DISCONNECTED

**Teams**（主題色：0 異常=綠、partial=橘、failed=紅）：

> NVR 掃描：partial
> 狀態 / 異常相機 / NVR 成功/失敗 / 耗時（fact list）

### 8.6 測試

#### 沒真實 webhook 的本機測試

1. **用 webhook.site**：https://webhook.site/ → 拿一個獨一 URL → 設為 `NVR_WEBHOOK_SLACK_URL` → 跑 `python nvr_scanner.py` → 看 webhook.site 收到 payload
2. **跑整合測試**（36 項）：
   ```bash
   pytest tests/test_webhook.py -v
   pytest tests/integration/test_e2e_webhook.py -v
   ```

#### 關閉 webhook

把 `enabled` 改 `false`（或把整個 `webhooks` 段設為 `[]`）。Worker 不會送任何 HTTP。

### 8.7 故障排除

| 症狀 | 原因 | 解法 |
|---|---|---|
| 沒收到通知 | `enabled=false` 或 `abnormal=0` | 確認設定；檢查 log |
| 收 400 `no_service` | Slack URL 拼錯 | 重新複製 Webhook URL |
| 收 403 / 404 | Teams connector 被刪除 | 重新建 connector |
| worker 沒結束但 webhook 卡住 | webhook 慢回應 | `webhook.WEBHOOK_TIMEOUT` 預設 10s；調成 5s 可加速 |
| log 看到 `[WARN] webhook 送出失敗` | 接收端回非 2xx | 屬預期；不會中斷 worker；只記到 `batch_result.webhook_results` |
