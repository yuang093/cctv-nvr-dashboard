# 部署說明（員工上手 cheatsheet）

目標：把 `dist/web/web.exe` 整包丟到任何 Windows 工作站（10/11），雙擊即可。
不需要裝 Python、不需要 venv、不需要 git。

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

---

## 升級

新版本發佈時：
1. 把現有 `nvr_scan.db` 保留（schema 沒變就 OK）
2. 砍掉舊 `web.exe` + `_internal/`
3. 複製新 dist/web/ 過去覆蓋
4. 重新啟動 exe

DB 自動保留 → 員工體驗無痛。
