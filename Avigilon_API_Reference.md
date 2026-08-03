# Avigilon Web Endpoint API 核心介接規格 (ACC 8.7+)

> 本文件整理自 Avigilon 原廠手冊，作為本專案（NVR 掃描器）實作時的 API 規格依據。
> 所有實作細節（endpoint 路徑、欄位名稱、錯誤處理）都應對齊本文件。
> 若與實測結果衝突，**以本文件為主**，並更新 `api_endpoints.md`。

## 1. 認證機制 (Authorization)

登入 Web Endpoint 必須動態生成 `authorizationToken`。**絕不能只傳帳號密碼**，否則伺服器會回 `403 Unknown reason`（即使帳密正確）。

- **必要材料**：
  - `userNonce`：由 Avigilon 核發給開發者的金鑰之一。
  - `userKey`：由 Avigilon 核發給開發者的金鑰之一。
  - `integrationId`：（選填）整合識別碼，若無則留空字串。
- **時間戳記**：取得當下的 Unix Time（秒級整數，例如 `1530726298`）。
- **雜湊計算 (`hexEncoded`)**：將字串 `timestamp + userKey` 進行 SHA-256 加密，並轉為 16 進位字串 (hex string)。
- **最終 Token 組合格式**：
  ```
  authorizationToken = userNonce + ":" + timestamp + ":" + hexEncoded + ":" + integrationId
  ```

### 1.1 Token 範例

```
userNonce   = "abc123"
userKey     = "secretkey"
timestamp   = 1530726298
integrationId = ""

hexEncoded  = SHA-256("1530726298secretkey").hexdigest()
            = "5d4e1f...（64 字元 hex）"

authorizationToken = "abc123:1530726298:5d4e1f...:"
```

## 2. 登入端點 (POST /login)

- **URL**：
  - ACC 一般版本：`https://<伺服器IP>:8443/login`
  - **ACC 8.7+**：`https://<伺服器IP>:8443/mt/api/rest/v1/login`（經實測確認）
- **HTTP Method**：`POST`
- **Content-Type**：`application/json`
- **Payload 格式**（注意：絕對不要使用 `accessToken` 欄位）：

  ```json
  {
    "username": "您的管理員帳號",
    "password": "您的密碼",
    "clientName": "PythonScannerApp",
    "authorizationToken": "【上述計算出的最終 Token 組合】"
  }
  ```

- **成功回傳**：伺服器將回傳包含 session 憑證的結果（欄位名為 `session`）。

### 2.1 常見錯誤

| HTTP Status | 原因 |
|---|---|
| 400 | 必填欄位缺失或格式錯（例如 `clientName` 為空、`userNonce` 缺值） |
| 401 | `username` / `password` 錯誤 |
| 403 | **缺少 `authorizationToken` 或欄位錯誤**，或帳號無 Web Endpoint 權限 |
| 500 | `clientVersion` 字串伺服器無法辨識（實測：需對齊 ACC 實際版本，如 `8.7.3.4`） |

## 3. 事件掃描 API

取得 session 後，在後續請求的 **Query Parameter** 帶入 `?session=<您的session>`。

### 3.1 取得伺服器 ID
- **端點**：`GET /mt/api/rest/v1/server/ids`
- **Query 參數**：`session=<token>`
- **回傳**：包含 `serverId` 的 JSON。

### 3.2 取得攝影機列表
- **端點**：`GET /mt/api/rest/v1/cameras`
- **Query 參數**：
  - `session=<token>` — 必填
  - `serverId=<id>` — 必填
  - `pageSize=<int>` — **強烈建議帶 100**，預設值極小會漏資料
- **回傳**：包裝在 `{status: success, result: {cameras: [...]}}`，每個 camera 含 `id` 與 `name` 欄位。
  ```json
  {
    "status": "success",
    "result": {
      "cameras": [
        {
          "id": "4xIx1DMwMLSwMDU0...",
          "name": "電子圍離物料暫存區-2",
          "apiType": "ONVIF_SOAP",
          "connectionState": "CONNECTED",
          "available": true
        }
      ]
    }
  }
  ```
  注意：相機物件的主鍵欄位是 `id`，**不是 `deviceId`**（舊版/其他端點可能不同）。

### 3.3 查詢當下事件
- **端點**：`GET /mt/api/rest/v1/events/search`
- **必填 Query 參數**：
  - `session=<token>`
  - `serverId=<id>`
  - `queryType=ACTIVE`（查詢當下未解除的異常事件）
  - `pageSize=<int>` — **建議帶 100**，ACTIVE 事件通常不多但防禦性加上
- **回傳**：ACTIVE 事件清單，包裝在 `{status: success, result: {events: [...]}}`（實測前先跑 `discover_event_subtopics.py` 確認實際 eventTopics 字串）。

## 4. 異常偵測（雙重檢查）

異常判定採**任一符合即標**：
1. **ACTIVE 事件**匹配下方關鍵字清單
2. **相機 `connectionStatus.state` 非 CONNECTED**

### 4.1 事件關鍵字（ACC 8.7 實測，DEVICE_* 開頭）

| 關鍵字 | 意義 |
|---|---|
| `DEVICE_VIDEO_SIGNAL_LOST` | 視訊訊號斷（影像線鬆脫、鏡頭故障） |
| `DEVICE_TAMPERING` | 鏡頭被遮、被破壞、被轉向 |
| `DEVICE_COMMUNICATION_LOST` | 通訊中斷（網路瞬斷） |
| `DEVICE_CONNECTION_ERROR` | 連線錯誤 |
| `DEVICE_LONG_FAILED` | 長期連線失敗（無回應超過門檻） |
| `DEVICE_DISCONNECTED` | 完全斷線 |
| `DEVICE_ANOMALY_START` | 影像分析偵測到場景異常 |
| `DEVICE_UNUSUAL_STARTED` | 未預期活動開始 |

> 對應原本規劃的 VIDEO_LOSS / TAMPER / BLIND / SCENE_CHANGE 概念對照：
> - VIDEO_LOSS → `DEVICE_VIDEO_SIGNAL_LOST`
> - TAMPER → `DEVICE_TAMPERING`
> - BLIND → `DEVICE_LONG_FAILED`（連線長期失敗 = 影像失效）
> - SCENE_CHANGE → `DEVICE_ANOMALY_START` / `DEVICE_UNUSUAL_STARTED`

### 4.2 connectionStatus.state 對照

| state 值 | 視為異常？ | 說明 |
|---|---|---|
| `CONNECTED` | ❌ 否 | 正常連線中 |
| `DISCONNECTED` | ✅ 是 | 設備已斷線 |
| `LONG_FAILED` | ✅ 是 | 長期失敗（ACC 實測斷網後常見此值） |
| `ERROR` | ✅ 是 | 連線錯誤 |
| `UNKNOWN` | ✅ 是（保守） | ACC 尚未確認狀態（剛啟動期間） |

實測 ACC 8.7 對斷網相機回 `LONG_FAILED`（不是 `DISCONNECTED`），故採「任何非 CONNECTED 都視為異常」的保守策略。

## 5. 安全提醒

- `userKey` 與 `userNonce` 為機密金鑰，**絕不能寫死在程式碼中**。
- 建議從環境變數讀取，或在執行時以互動方式輸入（密碼部分用 `getpass` 隱藏）。
- NVR 的 `username` / `password` 雖然不是金鑰，但仍是機密資料，遵循相同原則。
