# 部署說明（員工上手 cheatsheet）

目標：把 `dist/web/web.exe` 整包丟到任何 Windows 工作站（10/11），雙擊即可。
不需要裝 Python、不需要 venv、不需要 git。

> **Week 8 起**：本檔分**兩個模式**：
> 1. **30 秒極簡版**（下方）：內部 demo / exe 模式（保留原本 cheatsheet）
> 2. **部署模式 B：雙 App + HTTPS + Audit**（文末）：Week 5+ 正式部署（含 Flask-Login / rate-limit / audit_log / 8444 + 8555 雙 App 隔離）
>
> 新部署請直接跳到文末「部署模式 B」段。

## 30 秒極簡版

1. 把 `dist/web/` 整個資料夾複製到目標機器（例如 `D:\NVR\`）
2. **第一次**用系統管理員權限開 PowerShell，跑：
   ```
   D:\NVR\deploy\windows-firewall.ps1
   ```
   （開 port 8444 inbound，其他電腦才連得到）
3. 雙擊 `D:\NVR\web\web.exe`
4. 跳出黑窗，印出 LAN URL，找到 `http://192.168.x.x:8444/` 給同事
5. 同事瀏覽器打該 URL → 點「立即掃描所有 NVR」即可

> 員工 **不需要懂任何東西** —— 不需要 Python、不需要環境變數。

---

## 完整流程（管理者用）

### A. 在開發機器打包（一次性）

```powershell
cd C:\cc\NVR
.\build_web.bat
# 產物：dist\web\web.exe + dist\web\_internal\
# 整個 dist\web\ 資料夾就是要部署的東西
```

### B. 部署到目標工作站

#### 選項 1：直接複製（內網試用）

1. 把 `dist\web\` 複製到目標機器任意位置
2. 用 **NVR_DB_PATH** 環境變數告訴 exe 你的 DB 放在哪（或用預設同目錄 `nvr_scan.db`）

#### 選項 2：包成 zip 散佈

1. 把 `dist\web\` 壓成 `nvr-web-v1.zip`
2. 給同事 unzip 到 `D:\NVR\`
3. **首次**跑 `D:\NVR\deploy\windows-firewall.ps1`（系統管理員）
4. 雙擊 `D:\NVR\web\web.exe`

### C. 環境變數（選用）

| 變數 | 預設 | 用途 |
|---|---|---|
| `NVR_WEB_HOST` | `0.0.0.0` | 設 `127.0.0.1` = 只本機可連 |
| `NVR_WEB_PORT` | `8444` | 換 port（避開 8443 = NVR） |
| `NVR_DB_PATH` | 同目錄 `nvr_scan.db` | 放網路磁碟機路徑共享 |
| `NVR_WEB_NO_BROWSER` | （未設） | 設 `1` 關閉自動開瀏覽器 |

設法（PowerShell 永久）：
```powershell
[Environment]::SetEnvironmentVariable("NVR_WEB_PORT","9000","User")
```

---

## 疑難排解

### Q1：雙擊 web.exe 黑窗一閃即逝

**原因**：多半是 port 8444 已被佔用，或 DB 路徑錯誤。
**解法**：
1. 改用 cmd 開：`cmd /k D:\NVR\web\web.exe`，讓黑窗留下來看錯誤
2. 或開 PowerShell `cd D:\NVR\web ; .\web.exe` 看 traceback

### Q2：同事連得到 http://192.168.x.x:8444 但網頁開不起來 / 卡很久

**原因**：防火牆沒開。
**解法**：系統管理員 PowerShell：
```
D:\NVR\deploy\windows-firewall.ps1
```
或手動：
```
netsh advfirewall firewall add rule name=NVR-Web-8444 dir=in action=allow protocol=TCP localport=8444
```

### Q3：PDF 報告（/abnormal/export.pdf）下載變空 / 中文變框框

**原因**：員工機器沒 CJK 字型，或路徑不對。
**解法**：
- 先確認 `C:\Windows\Fonts\msjh.ttc` 或 `mingliu.ttc` 存在
- 沒有？裝 Office（內含微軟正黑體）或從其他機器 copy 過去

### Q4：資料庫零資料、想從頭開始

把現有 DB 砍掉、重新跑 `nvr_scanner.py` 一次（開發環境），web.exe 會讀到。

```powershell
del D:\NVR\nvr_scan.db
# 用開發 venv 跑一次建立資料
python -m nvr_scanner.py
# 然後再啟動 web.exe
```

### Q5：.env 內密碼怎麼辦

`web.exe` **不會內嵌 .env**（避免 commit 風險）。員工首次啟動若要讀 .env：
```
copy .env.example .env
notepad .env   # 填 AVIGILON_PASSWORD=...
```
同 exe 同一目錄下讀（環境變數優先於 .env）。

### Q6：怎麼改成開機自動啟動

用 Windows 工作排程器（Task Scheduler），不是登錄檔也不是服務：

```powershell
schtasks /create /tn "NVR-Web" /tr "D:\NVR\web\web.exe" /sc onstart /ru System
```

開機就會自動跑。崩潰不會重啟（要更強韌改用 nssm）。

---

## 與打包時的差異

| 項目 | dev 模式（venv） | exe 模式 |
|---|---|---|
| DB 路徑 | `NVR_DB_PATH` 或 `./nvr_scan.db` | 同上 |
| Templates 路徑 | 跟 .py 同目錄 | PyInstaller `_internal/web/templates`（自動偵測 sys._MEIPASS） |
| 改程式 | 改 .py + 重啟 | 改原始碼 → 重 build_web.bat → 重發佈 |
| 啟動 log | 中文 stderr | 同左，但 cp950 cmd 會亂碼（用 PowerShell 開就好） |

---

## 什麼時候不該用 exe

| 場景 | 建議 |
|---|---|
| 只是開發測試 | 直接 `.\run_web.ps1` |
| 部署到 Linux server | 不適用（PyInstaller exe 是 Windows） |
| 需要 hot reload / 改 hot patch | 用 dev 模式 venv |
| 要 service（崩潰自啟） | exe + Windows Task Scheduler onstart |
| 要 HTTPS / 帳號登入 / audit | 改用「部署模式 B」（下方）|

---

## 升級

新版本發佈時：
1. 把現有 `nvr_scan.db` 保留（schema 沒變就 OK）
2. 砍掉舊 `web.exe` + `_internal/`
3. 複製新 dist/web/ 過去覆蓋
4. 重新啟動 exe

DB 自動保留 → 員工體驗無痛。

---

# 部署模式 B：雙 App + HTTPS + Audit（Week 5+）

> **適用場景**：對外 / 跨網段 / 需登入 / 需追蹤誰做了什麼操作的生產環境。
>
> Week 5 起加入 Flask-Login + HTTPS + rate-limit + audit_log，部署摩擦大升級。
> Week 6 拆出 8444 dashboard + 8555 clips 雙 App 隔離（見 `docs/dual-app-isolation.md`）。
> 本段是「正式版」部署指南；上方「30 秒極簡版」保留給內部 demo。

## 架構總覽

```
                     Internet / LAN
                          │
                          ▼
                  ┌──────────────┐
                  │ Caddy (443)  │ reverse proxy + HTTPS + auto-TLS
                  └──────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
       127.0.0.1:8444          127.0.0.1:8555
       (dashboard app)         (clips app)
              │                       │
              └───────────┬───────────┘
                          ▼
                  nvr_scan.db
                  (SQLite, READ-ONLY by 兩 app)

       nvr_worker 排程：每 5 分鐘掃所有 enabled NVR
                   ↓ 寫 nvr_scan.db + audit_log（Week 5+）
                   ↓ 週日凌晨 03:00 自動歸檔（Week 4+）
```

## A. 前置：環境變數

| 變數 | 必填 | 用途 |
|---|---|---|
| `NVR_WEB_SECRET_KEY` | ✅ | 8444 session 簽章（用 `python -c "import secrets; print(secrets.token_hex(32))"` 產） |
| `NVR_CLIPS_SECRET_KEY` | ✅ | 8555 session 簽章（與 8444 **不可相同**） |
| `NVR_DB_PATH` | ✅ | nvr_scan.db 絕對路徑 |
| `NVR_WEB_ALLOWED_IPS` | 選填 | 內網白名單（CIDR），預設空 = 全開放（生產建議填） |
| `NVR_AUDIT_RETENTION_DAYS` | 選填 | audit_log 保留天數，預設 90 |
| `NVR_ARCHIVE_DIR` | 選填 | Week 4 歸檔產出目錄，預設 `./archives/` |

設法（PowerShell 永久）：

```powershell
[Environment]::SetEnvironmentVariable("NVR_WEB_SECRET_KEY", (python -c "import secrets; print(secrets.token_hex(32))"), "User")
[Environment]::SetEnvironmentVariable("NVR_CLIPS_SECRET_KEY", (python -c "import secrets; print(secrets.token_hex(32))"), "User")
```

## B. Caddy 反向代理 + auto-TLS

`Caddyfile`（放在部署機器 `/etc/caddy/Caddyfile` 或 `C:\caddy\Caddyfile`）：

```
nvr.example.com {
    reverse_proxy 127.0.0.1:8444
    encode gzip
}

clips.example.com {
    reverse_proxy 127.0.0.1:8555
    encode gzip
}
```

啟動：

```bash
caddy run --config Caddyfile
# 自動申請 Let's Encrypt 憑證
```

## C. 啟動 8444 + 8555 兩 process

```powershell
# 開兩個 terminal（或用 NSSM 包成兩個 service）
.\run_web.ps1      # 8444 dashboard
.\run_clips.ps1    # 8555 clips
```

兩個 process 各自讀 `NVR_DB_PATH` 同個 SQLite 檔（READ-ONLY）。
worker 寫入端透過 `NVR_WEB_NO_BROWSER=1` 跳過自動開瀏覽器。

## D. 排程 worker（含 audit + archive）

Windows 工作排程器：

```powershell
schtasks /create /tn "NVR-Worker" /tr "D:\NVR\run_worker.ps1" /sc minute /mo 5 /ru System
```

`run_worker.ps1` 會自動：
- 掃所有 enabled NVR
- 寫 `scan_runs` + `events` + `audit_log`（Week 5+）
- 若遇週日凌晨 03:00 → 觸發歸檔腳本（Week 4+）

## E. 升級順序（從 v1.0 升到 Week 8）

1. **DB 自動 migration**：Week 3-5 的 schema 變更都靠 `SqliteWriter._init_schema()` 啟動時自動跑（idempotent）
2. **不要砍 `nvr_scan.db`**：DB 升級是無痛的
3. **改 .ps1 啟動腳本**：舊版 `run_web.bat` 仍可用，但推薦改用 `.ps1`（UTF-8 native）
4. **驗證**：重啟後跑 `curl http://127.0.0.1:8444/apidocs/` 應看到 Swagger UI

## F. 疑難排解（Week 5+ 新增 5 種症狀）

| 症狀 | 原因 | 解法 |
|---|---|---|
| 訪問 `/login` 一直 403 | 內網 IP 不在 `NVR_WEB_ALLOWED_IPS` 白名單 | 加 CIDR 進 env var；或暫時設空（不推薦） |
| 登入後操作 5 分鐘內被 rate-limit 擋 | flask-limiter 預設 10/min per IP | 調 `NVR_RATE_LIMIT_PER_MIN`（預設 10） |
| audit_log 寫入失敗 → 500 | DB lock 或 schema 缺 `audit_log` 表 | 跑 `python -c "from db.sqlite_writer import SqliteWriter; SqliteWriter('./nvr_scan.db')"` 觸發 init_schema |
| HTTPS 憑證過期 | Caddy auto-TLS 出問題 | `caddy reload` 重新申請；檢查 DNS A record 是否仍指向伺服器 |
| Swagger UI 顯示 "Internal Server Error" | flasgger 0.9.7 + Flask 3.x 已知 bug（`root_path` 相容性） | 啟動 server 後手動驗證；CI 不驗 Swagger UI（已知限制） |
| clips 8444 session cookie 不能跨到 8555 | 兩 process 用不同 SECRET_KEY（**這是設計正確**） | 兩個 app 各自登入即可；不要嘗試共用 session |

## G. 與 Week 5 前的差異

| 項目 | Week 5 前 | Week 5+ |
|---|---|---|
| 訪問控制 | 無（任何 LAN 都可讀） | Flask-Login + 內網白名單 |
| 傳輸 | HTTP 明文 | HTTPS（Caddy auto-TLS） |
| Rate limit | 無 | flask-limiter 預設 10/min per IP |
| Audit | 無 | audit_log 表 + 30 項 pytest |
| 部署 | 單 process | 雙 process（8444 + 8555）+ 反向代理 |
| API 文件 | 手寫 `api_endpoints.md` | flasgger + Swagger UI 自動產生（45 條 routes） |

---

## 相關文件

- `docs/dual-app-isolation.md` — 為什麼 8444 + 8555 不合併（單點故障隔離）
- `docs/openapi-migration.md` — Week 7 從手寫到 flasgger 自動產生
- `docs/w8-acceptance-report.md` — Week 8 驗收報告 + metric
- `database_schema.md` §audit_log / §schema_migrations — Week 5 schema 變更
