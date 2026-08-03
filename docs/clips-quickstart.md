# Clips Server 快速上手（給另一部門用）

> Phase 2.7 影片片段調閱（port 8555）的最簡使用說明。
> 完整設計見 `docs/clip-feature.md`；Media API 規格見 `docs/media-api-research.md`。

---

## 1. 開頁面

伺服器已部署在 `http://<server>:8555/`（預設綁 0.0.0.0，LAN 內可達）。

直接打開 `http://<server>:8555/clips` 即可，**不需要登入**（clips server 自己用整合帳號登入 NVR）。

---

## 2. 怎麼用（兩段式 UX）

### Step 1：選 NVR + 輸入時間

| 欄位 | 怎麼填 |
|---|---|
| NVR | 下拉選單（DB 內所有啟用的 NVR）|
| 事件時間 | datetime-local；可用瀏覽器預設值（=現在）|

### Step 2：按「▶ 預覽快照」

- 畫面會一次抓該 NVR 下所有相機的 jpeg 縮圖
- **等 1-3 秒**，出現 N 格縮圖 grid
- 縮圖失敗的相機會顯示「❌ 無法取得」

### Step 3：點任一格 → 載 30 秒影片

- 點擊縮圖後，該格下方會展開 `<video>` 播放器
- 預設載入 ±15 秒共 30 秒的 mp4 stream
- 可按瀏覽器原生控制（暫停 / 跳秒 / 全螢幕）
- 想存檔：在 `<video>` 上按右鍵 → 「另存影片」（或開 DevTools 看 response header）

---

## 3. 常見問題

### Q1：選不到我想查的 NVR？

去 `http://<server>:8444/nvrs`（**8444** 是營運內部用），把那台 NVR 的 `enabled` 改成 ✓。
改完按「▶ 立即掃描」讓 DB 內 cameras 表更新（第一次掃會抓所有相機，之後只更新）。

### Q2：按「▶ 預覽快照」沒反應？

- **白名單**：瀏覽器 F12 → Console 看錯誤訊息
- **若 500 / 403**：NVR 整合帳號沒 External API 權限，找管理員
- **若 503 / timeout**：該 NVR 可能離線，按「▶ 立即掃描」看 dashboard 是否顯示離線

### Q3：縮圖有了但點下去沒影片？

- **黑名單**：NVR 該時段可能沒錄影（鏡頭被遮、斷電、HDD 滿）
- **嘗試 ±5 分鐘的其他時間點**重抓快照，確認時段對不對

### Q4：下載的 mp4 在公司電腦放不了？

- 確認 Windows Media Player 有裝 H.264 codec（Win10 預設有，Win7 需裝 K-Lite）
- VLC 一定可以播（推薦安裝）

---

## 4. 給管理員（部署 / 維運）

### 啟動 / 停止

```powershell
# 啟動（前景）
cd C:\cc\NVR
.\run_clips.ps1

# 背景啟動（不卡 terminal）
Start-Process powershell -ArgumentList "-NoProfile", "-File", "C:\nvr\run_clips.ps1" -WindowStyle Normal
```

### 環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `NVR_CLIPS_HOST` | `0.0.0.0` | 綁定 IP（要 LAN 才看得到別設 127.0.0.1）|
| `NVR_CLIPS_PORT` | `8555` | 埠號 |
| `NVR_CLIPS_CLIENT` | （空）| 設成 `mock` → 不打真 NVR，用 mock 回假資料（測試 / demo 用）|
| `NVR_CLIPS_DEBUG` | `False` | Flask 除錯模式（會自動重載）|

### 整合帳號設定

`/clips` server 用整合帳號登入 NVR。需要的環境變數（在 `.env`）：

```
AVIGILON_USER_NONCE=<整合帳號的 userNonce>
AVIGILON_USER_KEY=<整合帳號的 userKey>
AVIGILON_USERNAME=<覆寫 username，可選>
AVIGILON_PASSWORD=<覆寫 password，可選>
```

該帳號必須在 ACC 內有 **External API** 權限（讀 cameras + 讀 media）。
`.env` 已在 `.gitignore`，**絕對不要 commit**。

### 跟 8444 的差別

| 項目 | 8444（營運內部）| 8555（另一部門）|
|---|---|---|
| 用途 | Dashboard、掃描、CRUD NVR | 純調閱影片 |
| DB 寫入 | 有（CRUD + 掃描）| 無（`PRAGMA query_only=ON`）|
| Auth | 自己登入 | 整合帳號代登 |
| 對 NVR | 寫入 events/cameras/scan_runs | 只讀 media |

兩個 server **共用同一個 SQLite DB**，可以同時跑不會衝突。

### 出問題時

```powershell
# 看 8555 port 是否在 listen
Get-NetTCPConnection -LocalPort 8555 -State Listen

# 看最近的 log（啟動時的 console output）
# 如果背景啟動看不到 log，可改用 run_clips.ps1 前景啟動

# 確認 DB 路徑
ls C:\cc\NVR\nvr_scan.db
```

---

## 5. 限制 / TODO

- **不寫 audit log**（v2.7 預設不寫 `clips_log` 表，需要再加）
- **無使用者登入**：任何人打開都能查（適合內網；對外要加 reverse proxy + auth）
- **無下載計數**：要看誰調閱要進 DB 看 events 對應的 camera
- **NVR 整合帳號密碼定期 rotate** 需手動更新 `.env`（無 UI）

完整 spec / roadmap 見 `docs/clip-feature.md` §10。