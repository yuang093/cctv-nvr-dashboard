# NVR Media API — 研究筆記（**已驗證**：PDF 2026-05-15 規格書 v6）

> **狀態**：✅ **Endpoint 確認**，timestamp / duration 仍待 NVR 上線時驗證。
> 撰寫日期：2026-07-06。
> 來源：`web_endpoint_api_2026-05-15-16-34-39.pdf`（已用 `pdftotext` 解出全文存 `docs/pdf-full.txt`，Media API 段在行 1309–1491）

---

## 0. 摘要（TL;DR）

| 項目 | 確定值 |
|---|---|
| Media API endpoint | **`GET /mt/api/rest/v1/media`** （**與 REST API 同一個 port 8443**，不是 8555）|
| 認證 | **`?session=<token>`** — 與 `/login` 同一份 token |
| 路徑前綴 | `/mt/api/rest/v1/`（沿用既有命名空間）|
| 必填 query | `session`、`cameraId`、`format` |
| 已知的 format 值 | `mpd` `fmp4` `jpeg` `json` `webm` `spkc` |

> 因此推測 user 講的 **「Web UI 跑在 port 8555」** 是指**新 clip Web UI 自己的部署 port**，不是 NVR Media API port。NVR 端 Media 仍走 **port 8443**。

---

## 1. 6 種 format（含用法）

來源：PDF 第 43-51 頁（已抽取在 `docs/pdf-full.txt` 行 1309-1491）。

| format | 用途 | Response | 適用我們嗎？|
|---|---|---|---|
| **`mpd`** | **DASH manifest** — 第一步抓，回 XML 帶多個 `<BaseURL>` | XML `<MPD>` 含多個 Representation (`avc1.4D402A` H.264) | **✅ 主要流程**：抓 manifest，再用 BaseURL 帶 timestamp 取片段 |
| **`fmp4`** | **Fragmented MP4** 直接串流 — 一個 GET 回 MP4 串 | `video/mp4`, codec `avc1.*` | **✅ 備用**：直接打 30 秒 clip |
| `jpeg` | 單張 snapshot（加 `&t=live`）| 圖片 binary | ❌ 不適用（單張不是 30 秒）|
| `json` | feature vectors / bounding boxes（加 `&t=live&media=meta`）| NDJSON stream | ❌ 不適用（這是 AI 標註）|
| `webm` | audio stream | audio binary | ❌ 不適用（只要影片）|
| `spkc` | 查支援的 speaker codecs | JSON | ❌ 不適用 |

---

## 2. 推薦的「30 秒片段調閱」流程

兩條路徑，選一條就好：

### 2.1 路徑 A：DASH MPD（推薦，最靈活）

```
Step 1: 抓 manifest
GET /mt/api/rest/v1/media?session=<TOKEN>&cameraId=<CAM>&format=mpd
        → 回 <MPD> 含多 <Representation>；每個有 <BaseURL>

Step 2: 抓 representation 帶 timestamp seek
GET <BaseURL>&t=<ISO8601 timestamp>
        → 回 fragmented MP4，從指定時間點起
```

**優點**：HLS/DASH-style，瀏覽器原生 `<video>` 直接播、可 seek、可下載完整片段。
**缺點**：兩次 round-trip，並要解析 XML。

### 2.2 路徑 B：fmp4 直取（最簡）

```
GET /mt/api/rest/v1/media?session=<TOKEN>&cameraId=<CAM>&format=fmp4&t=<ISO8601>
        → 回 H.264 fragmented MP4 stream
```

**優點**：一次 GET 完成；最容易實作。
**缺點**：要看 NVR 是否支援 `t=` 歷史 seek（spec 只明示 `t=live`，但 extension 應該有，等 NVR 上線驗證）。

---

## 3. 對應的文件規範（spec 已確認）

來源：PDF `docs/pdf-full.txt` 行 1309-1491。

### 3.1 Base Request
```
GET https://<host>:8443/mt/api/rest/v1/media
    ?session=<SESSION>
    &cameraId=<CAMERA_ID>
    &format=<mpd|fmp4|jpeg|json|webm|spkc>
    [&t=<timestamp>]            # 可選；`live` 或 ISO 8601
    [&media=meta]               # json 專用
```

### 3.2 Sample for `format=fmp4` (line 1363)
```
https://localhost:8443/mt/api/rest/v1/
media?session=<session>&cameraId=<cameraId>&
format=fmp4
```

### 3.3 MPD Response (line 1405)
```xml
<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" ... profiles="..." type="static" minBufferTime="PT1.5S">
  <Period>
    <AdaptationSet>
      <Representation id="2" bandwidth="6311904" width="1777" height="..."
                      mimeType="video/mp4" codecs="avc1.4D402A">
        <BaseURL>/mt/api/rest/v1/media?ctx=...&session=...&cameraId=...</BaseURL>
      </Representation>
      <Representation id="3" bandwidth="1036800" width="720" height="480"
                      mimeType="video/mp4" codecs="avc1.4D4016">
        <BaseURL>...</BaseURL>
      </Representation>
      ...
    </AdaptationSet>
  </Period>
</MPD>
```

→ 多個 representation 是不同**解析度 / 編碼**串流；選一個 Representation 用其 `<BaseURL>` 再 GET。

### 3.4 BaseURL 帶時間 seek (line 1415, 1346)
```
<BaseURL>/mt/api/rest/v1/media?ctx=...&session=...&cameraId=...</BaseURL>
        ↓ 加上 timestamp
/mt/api/rest/v1/media?ctx=...&session=...&cameraId=...&t=<ISO8601>
```

Spec line 1346: `Additional parameters such as time-offset (t) can be appended to the BaseURL as needed.`

---

## 4. **不確定**（需 NVR 上線時驗證）

| # | 問題 | 假設 fallback |
|---|---|---|
| 1 | `format=fmp4&t=<ISO8601>` 是否真能 seek 到歷史時間？| spec 沒明示 `t=` 對 `fmp4` 是否有效。如不行，改用 mpd 兩步流程 |
| 2 | MPD manifest 內 `BaseURL` 的 `ctx=` token 生命期？| 假設跟 session 同生命期；過期就再抓一次 manifest |
| 3 | 一次能抓多長歷史片段？無上限？| spec 沒寫；先假設無限，必要時加 `&duration=30` 測試 |
| 4 | `t=` 用 Unix timestamp 還是 ISO 8601？| spec 範例用 `t=live`、jpeg 範例的 timestamp 是 `20200109T035040.843Z` 格式（compact ISO），建議兩種都試 |
| 5 | 同一時間並行多 session 是否對 NVR 有壓力？| 加 rate-limit（per-IP）|

---

## 5. 已修正的計畫決策

| 之前 | 之後 |
|---|---|
| 假設 Media API 在 port 8555 | **真實在 port 8443**，命名空間 `/mt/api/rest/v1/media` |
| 需設計獨立 Media auth | **沿用** `/login` 的 session token |
| 輸出格式是 MP4 / MJPEG 之一 | **`mpd` (DASH) 為主、`fmp4` 為輔**，兩者都基於 H.264 (`avc1.*`) |
| 需要 NVR 設定 `media_port` | **不需要**（Media API 跟 REST 同一 port，NVR config 結構不變）|

---

## 6. 工程實作對應（已更新）

### 6.1 protocol 介面（新增）
```python
# web/clip_retrieval.py
from typing import Protocol, Iterator
from datetime import datetime

class MediaApiClient(Protocol):
    """從 NVR Media API 取得攝影機片段。"""

    def get_mpd_manifest(self, camera_id: str) -> str:
        """GET .../media?format=mpd → 回 MPD XML 字串。"""

    def fetch_clip(
        self,
        camera_id: str,
        start_time: datetime,    # ±15s 的開始時間（UTC）
        end_time: datetime,      # ±15s 的結束時間
    ) -> Iterator[bytes]:
        """從指定秒數區間取 H.264 fragmented MP4 byte chunks。"""
```

### 6.2 Web UI 流程（更新）
```
User 選 NVR / 相機 / 時間
  ↓
POST /clips/fetch {nvr_id, camera_id, start_ts, end_ts}
  ↓
ClipRetrieval 用 NVR credentials login → session token
  ↓
get_mpd_manifest(camera_id) → MPD
  ↓
挑最低 bandwidth Representation（小檔案傳輸快）→ BaseURL
  ↓
GET <BaseURL>&t=<start_ts> → stream back to browser
  ↓
Browser <video src="data:..."> 或先存 ./clips/<uuid>.mp4 再回 download link
```

### 6.3 暫存策略（先 stream，落地可後加）
```python
# 簡易版：直接 stream response，不落硬碟
return Response(
    fetch_clip(...),
    mimetype='video/mp4',
    headers={'Content-Disposition': f'attachment; filename="{camera_id}_{ts}.mp4"'}
)
```
- NVR 下次有空間時：可改成寫到 `clips/<nvr_id>/<camera_id>/<ts>.mp4` 並寫 `clips_log` 表

---

## 7. 等 NVR 上線的第一動作

1. 驗證 Q1：先測 path 1（MPD），再測 path 2（fmp4+t=）
2. 跑 `python discover_media_api.py`（已寫好）做 path 列舉（雖然 spec 已說就 `/media`，但保險起見）
3. 在 `tests/test_clips_integration.py` 加真 HTTPS 整合測試（mock `AvigilonMediaClient`）

---

## 8. 整體可立刻做的（**不再等 NVR**，spec 已給齊）

| # | 項目 | 工時 |
|---|---|---|
| 1 | `web/clip_retrieval.py` — Protocol + `MpdClipClient` + `MockClipClient` | 0.3 d |
| 2 | `web/clips_app.py` — Flask 雛形（跑在 8555）| 0.3 d |
| 3 | `web/templates/clips.html` — 表單 + `<video>` 播放器 | 0.4 d |
| 4 | `database_schema.md` 加 `clips_log` 表 | 0.1 d |
| 5 | `run_clips.sh/.ps1/.bat` + 加到 `_write_bat.py` | 0.2 d |
| 6 | `tests/test_clips_app.py` + `tests/test_clip_retrieval.py` | 0.5 d |
| **合計** | | **~1.8 d** |

唯一**仍待 NVR 驗證**的是 `AvigilonMediaClient` 的 `fetch_clip()` 實作（~0.3 d，純代理，spec 已給完）。
