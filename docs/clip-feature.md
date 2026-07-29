# Feature Plan：影片片段調閱 (Clip Retrieval) — v2（加 snapshot first UX）

> Phase 2.7 — 新功能。
> **狀態**：✅ **計畫更新**（加入「先抓縮圖、點擊才放影片」的兩段式 UX；其他部分沿用 v1）。

---

## 1. 使用者故事 v2（兩段式）

**角色**：其他部門同事（"調閱者"）。

**流程（點縮圖才載影片，瀑布式 lazy）**：
1. 開啟 **http://<server>:8555/clips**
2. 從 NVR 下拉選單挑一台 → 輸入**事件時間**（datetime local）
3. 按下「▶ 預覽快照」→ **同時抓 N 台相機的 jpeg snapshot**
4. 畫面顯示 N 格縮圖（每台相機一張），標示時間
5. **點某一格** → 觸發 30 秒影片（fmp4 / mpd stream）載入到該格下方
6. 在 `<video>` 播放 / 或按「⬇ 下載」存成 `.mp4`

**優勢**：
- 一頁面看全部相機 → 不需要先選相機
- 快取 jpeg（多請求同時間不重打 NVR）
- 減少 30 秒影片請求數（只載 user 真正要看的）

---

## 2. 架構草圖 v2（兩段式資料流）

```
┌─────────────────┐         ┌───────────────────────┐         ┌──────────────────┐
│   Browser       │ ──────> │  Clip Web UI (8555)   │ ──────> │  Avigilon NVR    │
│   /clips 頁     │         │                       │         │  port 8443       │
└─────────────────┘         └───────────────────────┘         │  /mt/api/rest/v1 │
        │                              │                       │   + /media       │
        │  ① GET /clips                │                       └──────────────────┘
        │                              ▼
        │                       web/clip_retrieval.py
        │                              │
        │                              ├── MediaApiClient (Protocol)
        │                              │   ├── get_snapshot(camera_id, t) → jpeg bytes
        │                              │   ├── get_mpd_manifest(camera_id) → XML
        │                              │   └── fetch_clip(camera_id, start, end) → mp4 bytes
        │                              │
        │                              ├── MpdMediaClient (實作，待 NVR 上線驗)
        │                              │
        │                              └── MockMediaClient (本機測試用，回 fixture)
        │
        │                              │
        │  ② GET /clips/snapshots      │
        │     ?nvr_id=&t=<ISO8601>      │
        │     → { <camera_id>: <b64 jpeg>, ... }    (JSON dict of JPEGs)
        │
        │  ③ POST /clips/fetch         │
        │     {nvr_id, camera_id,       │
        │      start, end}              │
        │     ← Response stream mp4    │
```

### 2.1 新資料流（重寫 §3.3）

```
① 畫面初始：GET /clips → 顯示空表單
② User 按「▶ 預覽快照」
   → 瀏覽器 GET /clips/snapshots?nvr_id=X&t=YYYY-MM-DDTHH:MM:SSZ
   → ClipRetrieval.get_snapshot() × N 台相機（threading.ThreadPoolExecutor 並行）
   → 回 JSON dict {camera_id: base64-jpeg}（或 multipart）
   → 瀏覽器 render 縮圖 grid
③ User 點縮圖 i
   → 瀏覽器 POST /clips/fetch {nvr_id, camera_id, start, end}
   → ClipRetrieval.fetch_clip() 取 MPD（或直接 fmp4）
   → Flask stream response 給 <video>
```

---

## 3. 更新後的元件清單

### 3.1 新增檔案
| 檔案 | 用途 |
|---|---|
| `web/clips_app.py` | Flask app（port 8555）|
| `web/clip_retrieval.py` | `MediaApiClient` Protocol + 實作 |
| `web/templates/clips.html` | UI（縮圖 grid + 點擊放影片）|
| `tests/test_clip_retrieval.py` | Protocol/單元測試 |
| `tests/test_clips_app.py` | Flask 路由測試 |

### 3.2 修改檔案
| 檔案 | 加什麼 |
|---|---|
| `web/db.py` | 加 `list_cameras_for_nvr(internal_id)` 拿 camera_id + name |
| `nvr_config.json` 範例 | 加 `media_port` 預設 **8443**（已在 media-api-research.md 確認）|
| `database_schema.md` | 加 `clips_log` 表（audit）|
| `api_endpoints.md` | 加 §Media API 段（從 research.md 抄）|
| `class_interface.md` | 加 `MediaApiClient` 介面 |
| `_write_bat.py` | 加 `run_clips.bat/.sh` |
| `CLAUDE.md` | 加新入口 |

### 3.3 Routes（新增 port 8555 Flask app）

| Method | Path | 用途 |
|---|---|---|
| GET | `/` | 重導到 `/clips` |
| GET | `/clips` | 顯示表單（空 grid）|
| GET | `/clips/nvrs` | JSON 回所有 enabled NVR（給下拉用）|
| GET | `/clips/snapshots` | ?nvr_id=&t= → 回該 NVR 所有相機的 snapshot dict（並行 fetch）|
| POST | `/clips/fetch` | {nvr_id, camera_id, start, end} → stream mp4 |
| GET | `/clips/cameras` | ?nvr_id= → 該 NVR 的相機清單 |

---

## 4. MediaApiClient 介面（**已依 PDF 更新**）

來源：`docs/media-api-research.md`（從 `web_endpoint_api_2026-05-15-16-34-39.pdf` 抽出）。

```python
class MediaApiClient(Protocol):
    """對應 NVR Media API（/mt/api/rest/v1/media）。"""

    def get_snapshot(
        self,
        camera_id: str,
        at_time: datetime,            # UTC ISO 8601
    ) -> bytes:
        """GET ?format=jpeg&t=<time> → 一張 JPEG bytes。"""

    def get_mpd_manifest(
        self,
        camera_id: str,
    ) -> str:
        """GET ?format=mpd → XML MPD 字串。"""

    def fetch_clip(
        self,
        camera_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[bytes]:
        """從 MPD → BaseURL → 帶 t= 的 GET → fragmented MP4 stream bytes。"""
```

**Port**：**8443**（已從 PDF 確認，**不是** 8555）
**Auth**：沿用 `?session=<token>`（已從 PDF 確認）
**實作**：`MpdMediaClient` 用既有 `AvigilonScanner.login()` 拿 session，再 lazy 接 Media API

---

## 5. UI 互動設計（兩段式）

### 5.1 頁面初始
```
┌─ NVR ──────┐ ┌─ 時間 ──────────┐ ┌─ 預覽 ──┐
│ ACC8-P4 ▾  │ │ 2026-07-06 ... │ │  ▶      │
└────────────┘ └────────────────┘ └─────────┘
```
### 5.2 按下預覽後
```
┌─ 縮圖 grid（每台相機一格）────────────────┐
│  [前門]    [後門]    [倉庫]   [大門]      │
│  IMG     IMG        IMG       IMG          │
│  點我 → 變成下方的播放器                   │
├─ 載入中的影片區────────────────────────────┤
│  [▶ 30s 影片載入中... (用戶點了縮圖後)]     │
└────────────────────────────────────────────┘
```

### 5.3 點擊後
```
┌ 前門 ──────────────────────────┐
│  [▶ 30s mp4 video]              │
│  按鈕：[⬇ 下載 .mp4]            │
│  關閉：[✕]                       │
└──────────────────────────────────┘
```

---

## 6. 實作策略

### 6.1 Parallel snapshot fetch
多台相機同時打 NVR — 用 `concurrent.futures.ThreadPoolExecutor` 並行（不是 async）：

```python
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {
        ex.submit(client.get_snapshot, cid, at): cid
        for cid in camera_ids
    }
    snapshots = {cid: fut.result() for fut, cid in futures.items() if fut.exception() is None}
```

理由：Flask 是 sync；threading 比 asyncio 簡單；8 個 worker 對 10 台相機足夠。

### 6.2 NVR session 快取
Session token 從 `/login` 拿到後**自己快取**（避免每次 snapshot 都重 login）：
- in-memory dict：`{nvr_id: (token, fetched_at)}`
- TTL：30 分鐘（Avigilon 預設）
- 過期 → 自動重 login

### 6.3 Snapshot 回傳壓縮
JPEG 大小從原始 200KB 壓到 60×80 thumbnail（用 Pillow 縮圖）→ JSON base64 後 < 10KB/張 → 8 台相機 ~80KB 易傳輸。

### 6.4 Clip streaming
Flask `Response(fetch_clip(...), mimetype='video/mp4')`，
不要先 cache 整段到記憶體（NVR 可能串 1MB+）。

---

## 7. 完成準則（DoD）

- [x] user 提供 PDF → spec 已抽出（`docs/media-api-research.md`）
- [x] 兩段式 UX 整合進計畫（本檔）
- [ ] MediaApiClient Protocol + `MpdMediaClient` 佔位實作（path 已知）
- [ ] `MockMediaClient` 回 fixture（單元測試用）
- [ ] Flask app `web/clips_app.py` 跑在 8555（與 run_web 分離）
- [ ] 模板 `clips.html`（兩段式 grid + 點擊放影片）
- [ ] 並行 snapshot fetch（8 worker thread pool）
- [ ] Session token 快取（in-memory + 30 分鐘 TTL）
- [ ] Snapshot 縮圖壓縮（用 Pillow）
- [ ] 入口腳本 `run_clips.{sh,bat,ps1}`（_write_bat.py 生成）
- [ ] 測試：
  - Protocol/單元：`MockMediaClient` 走完 `get_snapshot` 與 `fetch_clip`
  - Flask 路由測試：page render、snapshots JSON、fetch 回應
  - 並行測試：8 台相機 fetch < 3 秒（mock 即可）
- [ ] 文件：`api_endpoints.md` §Media API、`class_interface.md` 加 Protocol、`database_schema.md` 加 `clips_log`、`todo_progress.md` 勾 v2.7

---

## 8. 不做（明確切開）

- ❌ 用戶登入 / RBAC（v2 待辦）
- ❌ MP4 落地保存（先純 stream；之後 audit 需要再加 `clips_log`）
- ❌ WebSocket 推播（v2 待辦）
- ❌ 真實 `MpdMediaClient` 完整實作（待 NVR 上線；spec 已給齊，主要 30 行內可寫完）

---

## 9. 時程粗估（人日）

| 階段 | 工作 | 估時 |
|---|---|---|
| Protocol + MockClient | 介面與 mock fixture | 0.3 d |
| Flask app + 3 條 route | clips_app + json + streaming | 0.4 d |
| 模板（兩段式 grid）| HTML + JS 點擊放 video | 0.4 d |
| 並行 snapshot fetch | ThreadPoolExecutor + 壓縮 | 0.2 d |
| Session cache | in-memory + TTL | 0.1 d |
| 入口腳本 + _write_bat | run_clips 三件 | 0.2 d |
| 測試 | 單元 + Flask route + 並行 | 0.5 d |
| 文件 | 4 份 .md 同步更新 | 0.2 d |
| **合計** | | **~2.3 d**（不含真 NVR `MpdMediaClient` 驗證）|

---

## 10. 時間軸（給 user 決策）

```
T=0   確認本計畫
T+0.5 寫 MediaApiClient Protocol + MockMediaClient ✅ 不需 NVR
T+1.0 寫 Flask app + 3 route + JSON 回傳 ✅ 不需 NVR
T+1.5 HTML + JS 兩段式 UI ✅ 不需 NVR
T+2.0 並行 fetch + session cache + 縮圖 ✅ 不需 NVR
T+2.3 入口 + 測試 + 文件 ✅ 不需 NVR
     ─────────────────────────────
T=N   NVR 上線時：把 MpdMediaClient 從「佔位」換成實作（~30 行程式）
T=N+0.5 整合測試 + 手動驗證 → 收工
```
