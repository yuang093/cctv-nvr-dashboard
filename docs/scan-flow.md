# NVR 掃描流程說明

## 掃描觸發方式

1. 員工在網頁點「▶ 立即掃描所有 NVR」
2. 瀏覽器 POST `http://127.0.0.1:8444/scan`
3. Flask 啟動背景執行緒（不回傳 HTML，不卡 UI）
4. 前端 JS 每 2 秒輪詢 `GET /scan/status` 查進度

## 掃描實際流程

```
瀏覽器 POST /scan
         ↓
背景緒執行 batch_scan()
         ↓
對每一台已啟用的 NVR：
  AvigilonScanner(host, port, user_nonce, user_key)
         ↓
  ┌─ 1. 登入驗證 ─────────────────────────┐
  │  POST /login                                  │
  │  SHA-256(password + nonce) → userKey        │
  │  → 拿 session cookie                        │
  └──────────────────────────────────────────────┘
         ↓
  ┌─ 2. 查所有攝影機清單 ──────────────────┐
  │  GET /cameras                                │
  │  → 每一台相機的 connectionStatus.state       │
  │  如果 state != "CONNECTED"                   │
  │     → 直接記為「異常相機」                  │
  └──────────────────────────────────────────────┘
         ↓
  ┌─ 3. 查所有 ACTIVE 異常事件 ───────────────┐
  │  POST /events/search                         │
  │  body: {"since": "..."}                   │
  │  → 只取 ACTIVE（未 resolved）的事件         │
  │                                           │
  │  只保留以下異常關鍵字：                     │
  │    DEVICE_VIDEO_SIGNAL_LOST  → 影像訊號斷線  │
  │    DEVICE_TAMPERING        → 破壞/遮蔽       │
  │    DEVICE_COMMUNICATION_LOST→ 通訊中斷       │
  │    DEVICE_CONNECTION_ERROR → 連線錯誤        │
  │    DEVICE_LONG_FAILED      → 長期失敗        │
  │    DEVICE_DISCONNECTED     → 斷線           │
  │    DEVICE_ANOMALY_START   → 影像分析異常   │
  │    DEVICE_UNUSUAL_STARTED  → 未預期活動     │
  │                                           │
  │  同時也檢查 cameras[].connectionStatus.state  │
  │  如果 state != "CONNECTED"                 │
  │     → 視為「異常相機」（即使沒事件）        │
  └──────────────────────────────────────────────┘
         ↓
  ┌─ 4. 寫入 SQLite ────────────────────────┐
  │  upsert_nvr()     → 確保 NVR 存在         │
  │  upsert_cameras() → 更新相機狀態           │
  │  insert_events()  → 寫入異常事件            │
  │                                           │
  │  去重邏輯：                                 │
  │  同 (nvr_id, device_id, event_topic)        │
  │  且 resolved_at IS NULL → UPDATE 不 INSERT   │
  │  （同一問題多次掃描不累積）                  │
  │                                           │
  │  mark_resolved() → 這次掃描發現已恢復的    │
  │                     相機，標記 resolved_at  │
  └──────────────────────────────────────────────┘
         ↓
  finish_scan_run() → 記錄這次掃描的狀態/時間
```

## 雙重保險機制

| 機制 | 抓到什麼 |
|---|---|
| **事件查詢** (`/events/search`) | NVR 主動產生的異常事件（訊號中斷、破壞、長期失敗等） |
| **Cameras 清單** (`/cameras`) | NVR 沒生事件，但 `connectionStatus.state != "CONNECTED"` 的相機 |

兩者獨立執行，確保不漏接。

## 寫入資料庫的內容

### events 表（異常事件）

| 欄位 | 說明 |
|---|---|
| `id` | 自增 ID |
| `scan_run_id` | 屬於哪一次掃描 |
| `nvr_id` | 所屬 NVR |
| `device_id` | 所屬相機 |
| `event_topic` | 異常類型（如 `DEVICE_VIDEO_SIGNAL_LOST`） |
| `occurred_at` | 事件發生時間（UTC） |
| `detected_at` | 掃描發現時間（UTC） |
| `resolved_at` | 解決時間（NULL = 仍未解決） |

### cameras 表（相機狀態）

| 欄位 | 說明 |
|---|---|
| `nvr_id` | 所屬 NVR |
| `device_id` | 相機 ID |
| `name` | 相機名稱 |
| `connection_status` | 連線狀態文字 |
| `last_seen` | 最後連線時間 |

## 故障類型中文對照

| 英文主題 | 中文 |
|---|---|
| `DEVICE_VIDEO_SIGNAL_LOST` | 影像訊號斷線 |
| `DEVICE_TAMPERING` | 破壞/遮蔽 |
| `DEVICE_COMMUNICATION_LOST` | 通訊中斷 |
| `DEVICE_CONNECTION_ERROR` | 連線錯誤 |
| `DEVICE_LONG_FAILED` | 長期失敗 |
| `DEVICE_DISCONNECTED` | 斷線 |
| `DEVICE_ANOMALY_START` | 影像分析異常 |
| `DEVICE_UNUSUAL_STARTED` | 未預期活動 |
| `STATE_DISCONNECTED` | 連線中斷 |
| `STATE_LONG_FAILED` | 長期失敗 |

## 去重邏輯（2026-07-03 實作）

同一台相機、同一個異常問題，在還沒解決的情況下：
- 第一次掃描 → INSERT 新事件
- 第二次掃描（問題仍 OPEN）→ UPDATE 事件時間，不新增列
- 問題解決後（resolved_at 有值）→ 下一個異常會再 INSERT 新列

這避免同一個未解決問題在多次掃描後累積大量重複列。

## 時區處理

- **資料庫**：統一儲存 UTC（ISO 8601，結尾 Z）
- **網頁顯示**：轉為 Asia/Taipei (UTC+8)
- **PDF 報告**：同樣顯示台北時間

## 常見問題

### Q：為什麼有些相機斷線了，但 NVR 沒產生事件？
可能原因：NVR 只在連線狀態改變時才會產生事件。如果相機一直斷線，NVR 仍維持一個 LONG_FAILED 狀態事件。若 NVR 完全沒有該相機的事件，`/cameras` 的 `connectionStatus.state != "CONNECTED"` 仍會被雙重檢查抓出。

### Q：掃描失敗會怎麼樣？
單台 NVR 失敗不會中斷整批，只會記錄 `scan_runs.status = 'partial'`。其他 NVR 仍會繼續掃描。

### Q：員工需要輸入密碼嗎？
在首次設定時由管理員填入 `nvr_config.json`（或透過網頁 UI 的編輯功能），之後員工只需要按「立即掃描」即可，不需要接觸密碼。
