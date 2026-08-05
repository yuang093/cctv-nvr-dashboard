# Spec G：Cam 健康趨勢圖（v0）

> **For agentic workers:** 實作前請讀此 spec + 跑 brainstorming → writing-plans 流程。本 spec 屬 v2 Web UI 範圍（**純前端 + 純 DB**，不需 NVR 上線即可實作 + 測試）。

**Goal:** 提供 8444 dashboard 內一個新頁 `/trends`，以 mini sparkline + 詳細大圖呈現每台 cam 過去 24h/7d 的「健康三維度」（連線/凍結/過暗），幫助 user 從「現在狀態」升級到「過去趨勢」判斷。

**Architecture:** 純 SQLite 查詢 + Chart.js 4.4.0 渲染（沿用 fleet.html 已引入）+ 新 Flask route `/trends`。**不引入新 schema**——用既有 `image_health_checks` 表代理「連線掃描記錄」（image_health 沒寫入 = 那段時間沒掃到 = 視為 offline）。

**Tech Stack:** Flask + Jinja2 + Chart.js 4.4.0（CDN）+ SQLite + pytest。

---

## 1. 動機（為什麼需要）

現有 dashboard / 設備總覽 / 異常頁只能看「現在 snapshot」：

| 頁面 | 顯示 | 缺什麼 |
|---|---|---|
| `/` (dashboard) | 最近 5 次 scan_runs | 不能看「某 cam 過去 24h 整體趨勢」 |
| `/abnormal` | 當下異常事件清單 | 「這 cam 昨天到現在壞了幾次？」看不出來 |
| `/devices` | cam 列表 + 最後檢查時間 | 「這 cam 一直閃斷嗎？還是剛壞？」分不出 |
| `/devices/<cam_id>` | 該 cam 最近檢查詳情 | 無歷史圖表 |

User 痛點（從之前 session 觀察）：
- Cam3 ghost 過濾：user 想知道「最近 24h 有沒有再出現？」
- Cam1 frozen：user 想知道「是偶爾還是持續？」
- NVR 離線：user 想知道「離線了多久？」

**解法**：單一頁面 `/trends` 把每台 cam 趨勢一覽無遺。

## 2. 設計決策

### 2.1 健康三維度（單台 cam 的 sparkline 三條線）

每台 cam 圖表 3 個系列（不同 Y 軸或 normalization）：

| 系列 | 語意 | 資料來源 | 計算 |
|---|---|---|---|
| **Online** | 該時段有沒被掃到（proxy 連線狀態） | `image_health_checks.checked_at_utc` | 若該 bin 有任一記錄 → 100%；無 → 0% |
| **Frozen** | 該時段有幾次 frozen | `image_health_checks.flags_json`（`is_frozen: true`） | count / 該 bin 總次數 |
| **Dark** | 該時段有幾次 dark scene | `image_health_checks.flags_json`（`is_dark_scene: true`） | count / 該 bin 總次數 |

**設計取捨**：用「該時段佔比」而非「絕對次數」——避免「整天沒掃」（分母=0）vs「整天狂掃」（分母大）造成視覺誤判。

### 2.2 Schema 決策：**不加新表**

候選：
- **方案 A（推薦 ✅）**：零 schema 改動，image_health_checks 代理「連線掃描記錄」。理由見 #2.1 + YAGNI。
- **方案 B（拒絕）**：新增 `cam_connection_history` 表，每次 scan 寫入。優點：語意直白；缺點：+30 行 migration + 整合測試 + 之後 dashboard / devices 也要改寫用新表。

**未來後悔成本低**：若 image_health 推斷不準確（例如 batch_scan 對離線 NVR 跳過 image_health stage），再加 schema B 也不影響既有資料。

### 2.3 範圍（YAGNI 嚴格控制）

**做**：
- 新路由 `GET /trends`
- 新模組 `web/trends.py`：2 個 public 函式
  - `compute_health_timeseries(cam_id: str, range_hours: int) -> list[HealthBin]`
  - `get_all_cams_health_summary(range_hours: int, nvr_filter: str | None, status_filter: str | None) -> list[CamHealthSummary]`
- 新模板 `web/templates/trends.html`：mini sparkline grid（每 cam 一張卡）
- Range toggle（24h / 7d，URL query param `?range=24h|7d`）
- NVR filter（dropdown，URL `?nvr_id=X`）
- Status filter（any / abnormal_only，URL `?status=abnormal_only`）
- 點 sparkline → inline 展開詳細 chart（**不**用 modal——更輕量、響應式友善）
- 按「異常時數」降冪排序（最需要關注的 cam 在最上方）
- 異常 cam 高亮（紅色框線）

**不做**（明確排除）：
- Modal 彈窗（用 inline 展開取代）
- 即時 polling / WebSocket（純頁面 reload）
- CSV 匯出
- 異常事件 overlay（user 沒選 image + events 組合）
- 12 theme dark 顏色微調（沿用 Chart.js 預設色票，之後再說）
- 跨 NVR 排行（屬 Spec H 之後）
- 自訂 threshold / alert 規則（屬 v3）

### 2.4 與既有頁面整合（深鍵結）

讓 user 從 dashboard / coverage / devices 直接跳到 trends：

| 出發頁 | 連結形式 |
|---|---|
| `/devices` 每台 cam 行右側 | `<a href="/trends?cam_id={id}&range=24h">📈</a>` |
| `/` (dashboard) 異常 cam 列表每行 | 同上 |
| `web/clips_templates/coverage.html` 綠帶上方 cam 名 | 同上 |
| `web/templates/base.html` navbar | `<a href="/trends">📈 健康趨勢</a>` |

不影響既有功能（純加 `<a>`，不改既有路由/邏輯）。

### 2.5 命名

| 項目 | 值 |
|---|---|
| URL | `/trends` |
| Module | `web/trends.py` |
| Template | `web/templates/trends.html` |
| Navbar 連結文字 | `📈 健康趨勢` |
| Functions | `compute_health_timeseries` / `get_all_cams_health_summary` |
| Dataclasses | `HealthBin` / `CamHealthSummary` |

## 3. 架構

```
[GET /trends]
   ↓
   ↓ Flask route handler (web/app.py 新增)
   ↓
   ↓ get_all_cams_health_summary(range, nvr_filter, status_filter)
   ↓
   ↓ web/trends.py (純 DB 查詢)
   ↓
   ├─ 列出所有 cam（JOIN cameras + nvr_servers，過濾 ghost）
   ├─ 對每台 cam 呼叫 compute_health_timeseries(cam_id, range)
   │     ↓
   │     ├─ 從 image_health_checks 查該 cam 的所有記錄
   │     ├─ bin by hour (24h) 或 day (7d)
   │     └─ 每 bin 算 {online%, frozen%, dark%}
   ├─ 算每台 cam 的「異常時數」(frozen/dark/offline 任一 > 0)
   └─ 回傳 list[CamHealthSummary]
   ↓
   ↓ render_template('trends.html', summaries=..., range=...)
   ↓
   ↓
[trends.html]
   ├─ Range toggle: <a href="?range=24h">24h</a> | <a href="?range=7d">7d</a>
   ├─ NVR dropdown (從 summaries 取所有 nvr)
   ├─ Status filter (any / abnormal_only)
   └─ Loop over summaries:
        ├─ Cam card (name + NVR + 異常時數)
        ├─ Mini sparkline (3 series, Chart.js)
        └─ 點 → inline 展開詳細 chart (同 chart 放大)
```

## 4. 元件規格

### 4.1 `web/trends.py`

```python
@dataclass(frozen=True)
class HealthBin:
    """一個時間 bin 的健康統計。"""
    start_utc: str          # ISO 8601 e.g. "2026-08-05T14:00:00Z"
    online_pct: float       # 0.0-100.0
    frozen_pct: float       # 0.0-100.0
    dark_pct: float         # 0.0-100.0
    sample_count: int       # 該 bin 內 image_health 記錄數


@dataclass(frozen=True)
class CamHealthSummary:
    """一台 cam 的健康摘要（給 trends.html 列表用）。"""
    cam_id: str
    cam_name: str
    nvr_id: str
    nvr_name: str
    bins: list[HealthBin]
    abnormal_bins: int      # 異常時數（frozen/dark/offline > 0）


def compute_health_timeseries(
    db_path: str,
    cam_id: str,
    range_hours: int,       # 24 或 168 (7d)
) -> list[HealthBin]:
    """從 image_health_checks 算 cam 的時間序列健康資料。

    24h → 24 個 1-hour bins
    7d  → 7 個 24-hour bins（每天一個聚合）

    Args:
        db_path: SQLite DB 路徑
        cam_id: 相機 ID
        range_hours: 24 或 168

    Returns:
        由舊到新排序的 list[HealthBin]，長度 = range_hours / bin_size
    """


def get_all_cams_health_summary(
    db_path: str,
    range_hours: int = 24,
    nvr_filter: str | None = None,
    status_filter: str = "any",  # "any" / "abnormal_only"
) -> list[CamHealthSummary]:
    """列所有 cam（含 NVR JOIN），各呼叫 compute_health_timeseries，
    算出每台的 abnormal_bins。

    Args:
        db_path: SQLite DB 路徑
        range_hours: 24 或 168
        nvr_filter: 限定單一 NVR（None = 不限）
        status_filter: "any" = 全部 / "abnormal_only" = 只列 abnormal_bins > 0

    Returns:
        按 abnormal_bins DESC, cam_name ASC 排序的 list
    """
```

### 4.2 `/trends` route（web/app.py）

```python
@app.route("/trends")
def trends():
    """Cam 健康趨勢總覽（Spec G）。"""
    range_hours = int(request.args.get("range", "24"))
    if range_hours not in (24, 168):
        range_hours = 24
    nvr_filter = request.args.get("nvr_id") or None
    status_filter = request.args.get("status", "any")

    summaries = get_all_cams_health_summary(
        _get_db_path(app),
        range_hours=range_hours,
        nvr_filter=nvr_filter,
        status_filter=status_filter,
    )
    # 取所有 NVR 名單給 dropdown
    nvrs = webdb.list_enabled_nvrs(_get_db_path(app))

    return render_template(
        "trends.html",
        summaries=summaries,
        range_hours=range_hours,
        nvrs=nvrs,
        current_nvr=nvr_filter,
        current_status=status_filter,
    )
```

### 4.3 `web/templates/trends.html`

結構（不展開 CSS，僅描述）：
- `<h2>📈 Cam 健康趨勢</h2>`
- Range toggle 按鈕（active 樣式依 range 切換）
- NVR dropdown（select + onchange=submit form）
- Status filter checkbox（abnormal_only）
- Grid of cam cards：
  - 每張卡：cam 名 + NVR 名 + 異常時數 badge（紅色 if > 0）
  - Mini canvas（Chart.js line chart，3 series）
  - 點 canvas → 用 JS toggle 下方 inline detail chart
- 底部空狀態：「目前沒有 cam 紀錄」或「篩選條件下沒有資料」

### 4.4 Chart.js 設定

每張 sparkline 用：
- type: 'line'
- data: `{ labels: [bin.start_utc], datasets: [{online}, {frozen}, {dark}] }`
- options:
  - responsive: true
  - maintainAspectRatio: false
  - legend: false（mini 卡不需要）
  - tooltips: enabled
  - scales: `{ y: { min: 0, max: 100 } }`
  - elements: `{ point: { radius: 0 }, line: { borderWidth: 1.5 } }`

顏色（從 CSS var 讀取，保持 theme 自動套色）：
- online: `var(--success, #22c55e)`
- frozen: `var(--warning, #f59e0b)`
- dark: `var(--danger, #ef4444)`

## 5. 資料流程（單台 cam 例子）

```
NVR 每 ~30 秒掃一次 image_health（batch_scan._IMAGE_HEALTH_ENABLED）
   ↓
image_health_checks 表持續累積（每 cam 每 30 秒 1 筆）
   ↓
compute_health_timeseries(cam_id, 24)
   ↓
SELECT checked_at_utc, flags_json
FROM image_health_checks
WHERE camera_id = ? AND checked_at_utc >= datetime('now', '-24 hours')
   ↓
Python bin 處理：
   bin_size = 1h → 24 bins
   對每筆：
     算 bin index = (utc - start_utc) // 1h
     parse flags_json:
       is_frozen = json['is_frozen']
       is_dark_scene = json['is_dark_scene']
     累加 frozen_count / dark_count / total_count
   ↓
對每 bin：
   online_pct = (total_count > 0) ? 100 : 0
   frozen_pct = frozen_count / total_count * 100  (若 total = 0 → 0)
   dark_pct = dark_count / total_count * 100
   ↓
回傳 list[HealthBin]
```

## 6. 錯誤處理

| 錯誤 | 處理 |
|---|---|
| `range` query param 不是 24 / 168 | 退回 24h |
| `cam_id` 不存在 | compute_health_timeseries 回空 list（不丟例外） |
| `image_health_checks` 沒任何記錄（cam 從未被掃） | 所有 bin 都 offline → 100% offline 圖 |
| `nvr_id` 不存在 | 該 filter 不顯示在 dropdown；query 結果為空 |
| `flags_json` 解析失敗 | 該筆 fallback 為「非 frozen、非 dark」（避免 crash） |

## 7. 測試策略

### 7.1 純函式測試（`tests/test_trends.py`）

| 測試 | 驗證 |
|---|---|
| `test_compute_health_timeseries_24h_empty_db` | 沒記錄 → 24 個 bin 全 0% / 0% / 0% / count=0 |
| `test_compute_health_timeseries_24h_all_healthy` | 24 個 bin 各有 2 筆 → online=100%, frozen=0%, dark=0% |
| `test_compute_health_timeseries_24h_frozen_spike` | 第 5-7 bin 各有 1 筆 is_frozen → 那 3 bin frozen=100% |
| `test_compute_health_timeseries_24h_dark_spike` | 同上 dark |
| `test_compute_health_timeseries_24h_offline_window` | 第 10-12 bin 沒記錄 → online=0%（離線） |
| `test_compute_health_timeseries_7d_aggregates_to_daily` | 7d 應回 7 個 bin（每天一個） |
| `test_compute_health_timeseries_flags_json_parse_error_fallback` | 壞 JSON → 該筆 fallback，不 crash |
| `test_get_all_cams_health_summary_filters_ghost` | ghost cam 不在結果中 |
| `test_get_all_cams_health_summary_nvr_filter` | nvr_filter=ACC8 只列該 NVR |
| `test_get_all_cams_health_summary_abnormal_only` | status_filter=abnormal_only 只列異常 cam |
| `test_get_all_cams_health_summary_sorted_by_abnormal_bins_desc` | 排序正確 |
| `test_get_all_cams_health_summary_empty_db` | 空 DB 回空 list（不 crash） |

### 7.2 Route 整合測試（`tests/integration/test_e2e_trends.py`）

| 測試 | 驗證 |
|---|---|
| `test_trends_route_returns_200` | GET /trends → 200 + 含 "Cam 健康趨勢" |
| `test_trends_route_default_range_24h` | 不帶 query → 24h 模式 |
| `test_trends_route_range_7d_query` | `?range=7d` → URL 顯示 active 樣式 |
| `test_trends_route_nvr_filter` | `?nvr_id=X` → 只列 X 的 cam |
| `test_trends_route_status_filter` | `?status=abnormal_only` → 只列異常 |
| `test_trends_route_with_seeded_data_shows_bins` | seed image_health → 模板內含 sparkline canvas |
| `test_trends_route_invalid_range_falls_back_to_24h` | `?range=99` → 24h（不 500） |
| `test_trends_route_nvr_dropdown_lists_all` | template 內含所有 NVR 名（給 dropdown） |
| `test_trends_route_abnormal_badge_visible` | 異常 cam 有 "abnormal" badge class |

### 7.3 視覺驗證（Playwright）

啟動 8444 + seeded DB：
1. 截圖 `/trends` 全頁 → 確認 sparkline 顯示
2. 點 sparkline → 截圖展開狀態
3. 切 24h ↔ 7d → 截圖
4. 異常 cam 高亮 → 截圖

## 8. 風險與緩解

| 風險 | 緩解 |
|---|---|
| Chart.js 4.4.0 已引入，衝突風險低 | 沿用 CDN，theme 透過 CSS var 自動套色 |
| 沒真實 image_health 資料 → 圖都是空 | 整合測試用 seeded fixture，視覺驗證用 mock |
| image_health 沒寫入 ≠ NVR 離線（可能是 batch_scan 跳過 stage） | spec 文件說明「推斷限制」，UI tooltip 顯示 |
| 12 theme 顏色可能跟 Chart.js 預設色撞色 | 第一版用 CSS var fallback，之後再細調 |
| `/trends` 沒 nav link → user 找不到 | 必加 navbar 連結（purely additive） |
| 30 台 cam × 24 bin × 3 series = 2160 點/頁 | Chart.js 對小數據量沒問題（< 5k 點） |
| 7d 模式若 batch_scan 只跑 24h（image_health 沒持續）會看不到資料 | 文件說明 user 需開 `_IMAGE_HEALTH_ENABLED=1` |

## 9. 不做（明確 scope 排除）

- ❌ Modal 彈窗（用 inline 展開）
- ❌ 即時 polling / WebSocket（純頁面 reload）
- ❌ CSV / JSON 匯出
- ❌ 異常事件 overlay（image + events 組合）
- ❌ 12 theme 顏色微調（沿用預設）
- ❌ 跨 NVR 排行（屬 Spec H）
- ❌ 自訂 threshold / alert 規則（屬 v3）
- ❌ 新 schema（cam_connection_history 等）
- ❌ 改既有 dashboard / devices / coverage 邏輯（只加 link，不改行為）

## 10. 開放問題（暫不解決）

- Cam ghost 過濾：`cameras.is_ghost = 1` 過濾是否足夠？（spec 假設是）
- batch_scan 跳過 image_health stage 時 offline 推斷會誤判（spec 文件說明限制）
- 將來是否要把「online%」正規化成 Y 軸 0-100，把 frozen% / dark% 顯示在右軸？（目前 spec 用同一 Y 軸 0-100）

---

**Status**: Draft，user 已通過 brainstorming 4 個澄清問題 + 設計草案確認。

**Next**: writing-plans skill 寫實作計畫。
