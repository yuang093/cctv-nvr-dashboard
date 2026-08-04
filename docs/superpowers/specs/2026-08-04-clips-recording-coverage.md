# Spec F：8555 錄影覆蓋熱區（Recording Coverage Heatmap）

> **日期**：2026-08-04
> **作者**：Claude（brainstorming → spec）
> **狀態**：🟡 Draft → 🟢 Approved
> **從屬**：Phase 2.7 影片片段調閱延伸（Feature：找可回放時間點）

---

## Context

8555 影片片段調閱（Phase 2.7）目前流程：

1. 選 NVR → 選 cam → 選時間 → 撥放
2. **痛點**：使用者不知道該 cam 過去 24 小時**有沒有錄影**、**什麼時候有**
3. 結果：盲選時間、撥 30 秒沒畫面、再換時間… 重複操作

**目標**：在 8555 加一個「錄影覆蓋熱區」頁面，**一眼看出 1 台 NVR 所有 cam 過去的錄影時間分佈**，支援：
- 自訂時間範圍
- 點擊時間軸區段 → 跳轉 clips 頁（帶 NVR/cam/timestamp） → 一步看回放
- Hover tooltip 顯示該時段長度

---

## Goal

- ✅ 8555 新頁 `/clips/coverage` 顯示 1 台 NVR 所有 cam 的錄影時間軸
- ✅ 視覺：每 cam 1 行、每分鐘 1px 寬、綠帶 = 有錄影、空白 = 無錄影
- ✅ Hover 顯示「該錄影區段起訖 + 長度」tooltip
- ✅ 點擊區段 → 跳 `/clips?nvr_id=...&cam_id=...&t=...`（帶時間）
- ✅ 自訂時間範圍（2 個 datetime 輸入 + 預設值「最近 24h」）
- ✅ 顯示每 cam 的完整率（用 `web/timeline.py:compute_completeness()`）
- ✅ 8555 獨立呼叫 NVR `/timeline` API（**不讀 8444 DB**）

---

## Architecture

### 1. 整體流程

```
使用者訪問 /clips/coverage
  ↓
頁面預設顯示「最近 24h」+ 啟用的第一台 NVR
  ↓
前端 fetch GET /clips/coverage?nvr_id=<id>&start=<ISO>&end=<ISO>
  ↓
後端 coverage.fetch_coverage()：
  1. 從 DB 拿 NVR 連線資訊（host / port / credentials）
  2. AvigilonScanner.get_timeline(camera_ids, start, end)
  3. parse_timeline_response() 解析 NVR 回傳
  4. 對每 cam 計算完整率
  5. 回 JSON
  ↓
前端 render 1 個 cam-list：
  - 每 cam 1 行時間軸
  - Hover → tooltip
  - Click → 跳 /clips
```

### 2. 模組邊界

| 檔案 | 職責 |
|---|---|
| `web/coverage.py`（新）| 純邏輯：fetch NVR `/timeline`、解析、計算完整率、回傳 dict |
| `web/clips_app.py`（改）| 加 1 個路由 `/clips/coverage`（GET 頁面）+ 1 個 `/clips/coverage/data` JSON API |
| `web/clips_templates/coverage.html`（新）| 完整頁面（獨立 navbar + dark toggle + 時間軸 UI） |
| `web/timeline.py`（既有）| 複用 `parse_timeline_response()`、`compute_completeness()` |

**重點**：`web/coverage.py` 純邏輯 **不碰 Flask**（跟 `web/timeline.py` 風格一致），方便 unit test。

### 3. 資料來源

- **NVR 端**：`GET /api/v1/timeline?rangeStartMs=...&rangeEndMs=...&cameraIds=...`（既有 AvigilonScanner 支援）
- **不用 8444 DB**：保持 8555 獨立部署（Spec F 明確決策）

### 4. UI 設計

```html
<!-- 完整頁面結構 -->
<nav>📼 錄影覆蓋熱區 · Clips 8555</nav>

<div class="controls">
  <label>NVR: <select>...</select></label>
  <label>起: <input type="datetime-local"></label>
  <label>迄: <input type="datetime-local"></label>
  <button>更新</button>
  <span>完整率：87.5% (avg)</span>
</div>

<div class="timeline-list">
  <div class="cam-row">
    <div class="cam-name">Cam 1 (008888)</div>
    <div class="cam-bar">
      <div class="record-block" data-start="..." data-end="..." style="left:5%; width:80%"></div>
    </div>
    <div class="cam-stats">87.5%</div>
  </div>
  <!-- 重複每 cam -->
</div>

<footer>圖例：▓ 有錄影  ░ 無錄影</footer>
```

**時間軸實作**：
- 容器寬度固定 1440px（24h × 60min，1 分鐘 = 1px）
- 每個錄影區段 = 1 個 `<div class="record-block">`，絕對定位 + `left/width` 百分比
- 點擊該 div → JS 跳轉 clips 頁
- Hover → `title` 屬性顯示「03:24 - 03:58 (34 分鐘)」

### 5. 點擊互動

`/clips?nvr_id=53&cam_id=4xIx1D...&t=2026-08-04T03:24`

跟現有 clips 頁 dropdown 互動（既有 `/clips` GET 路由需支援 `?nvr_id&cam_id&t` query，符合現行 NVR 邏輯即可）。

---

## Scope

### ✅ 做（In Scope）

- 1 個新檔 `web/coverage.py`（純邏輯 + 4 個函式）
- 1 個新檔 `web/clips_templates/coverage.html`（獨立頁面）
- `web/clips_app.py` 改 2 處：
  - 加 `from web.coverage import fetch_coverage`，加 2 個路由（GET /clips/coverage, GET /clips/coverage/data）
  - navbar 加「📼 錄影熱區」連結
- 預設值 input date-time（首次載入 = 現在 - 24h ~ 現在）
- 完整率顯示（每 cam 1 個 %）
- TDD：預計 +12 個測試（coverage 純邏輯 4 + API 4 + 頁面 4）
- Dark mode 內聯（跟 8555 風格一致）
- 行內 CSS + JS（無 CDN 額外）
- navbar 連到 `/clips` 與 `/nvrs/`

### ❌ 不做（Out of Scope）

- ❌ 跨 NVR 比較（browsing focus = 1 台）
- ❌ 月份 heatmap（只做 24h 時間軸）
- ❌ 自動 refresh（使用者按更新）
- ❌ 抓 snapshot 縮圖（純時間軸）
- ❌ DB 寫入（純讀 NVR）
- ❌ 8555 session / cache（spec 內不做）
- ❌ 不改 8444 / 不動 DB schema

---

## File Changes

### 新增（3 個）

```
web/coverage.py                          # 純邏輯模組
web/clips_templates/coverage.html        # 頁面模板
tests/test_coverage.py                   # 純邏輯測試
tests/test_coverage_endpoint.py          # API 端點測試
```

### 修改（1 個）

```
web/clips_app.py                         # 加 2 路由 + 1 navbar 連結
```

### 新增測試（預計 +12）

```
test_coverage.py:
  - test_parse_timeline_to_records_matches_timeline_py
  - test_compute_per_camera_completeness_empty_returns_zero
  - test_compute_per_camera_completeness_full_returns_one
  - test_fetch_coverage_returns_dict_with_cameras_list

test_coverage_endpoint.py:
  - test_coverage_data_endpoint_returns_json
  - test_coverage_data_endpoint_404_when_nvr_disabled
  - test_coverage_data_endpoint_400_on_bad_window
  - test_coverage_data_endpoint_502_when_nvr_unreachable
  - test_coverage_endpoint_renders_html
  - test_coverage_page_has_nvr_dropdown
  - test_coverage_page_has_record_block_render_js
  - test_coverage_page_has_dark_toggle
```

---

## Verification

### 自動驗證

```bash
pytest -q
# 預期 852 → ~864 (+12 新測試)
```

### 手動驗證

```bash
# 重啟 8555
powershell -Command "Get-NetTCPConnection -LocalPort 8555 | Stop-Process -Force"
PYTHONIOENCODING=utf-8 nohup powershell -Command "Start-Process -FilePath 'python' -ArgumentList '-m','web.clips_app','8555' -RedirectStandardOutput 'nvr_clips_8555.out' -RedirectStandardError 'nvr_clips_8555.err' -WorkingDirectory 'C:\cc\NVR' -WindowStyle Hidden" &

# 訪問頁面
curl -sS http://127.0.0.1:8555/clips/coverage | grep -c "錄影熱區"
# 預期 >= 1

# API 測試
curl -sS "http://127.0.0.1:8555/clips/coverage/data?nvr_id=53&start=2026-08-04T00:00:00&end=2026-08-04T12:00:00" | python -c "import json,sys; print(json.load(sys.stdin))"
# 預期 { cameras: [{cam_id, records, completeness}], ... }
```

### 視覺驗證（Playwright）

- 開 `/clips/coverage` → 看到 NVR dropdown + 時間選擇器 + 時間軸 UI
- 點「更新」→ 確認時間軸 render 出綠帶
- Hover 綠帶 → tooltip 顯示時間
- 點綠帶 → 跳到 `/clips` 帶正確 query

---

## Risks

| 風險 | 緩解 |
|---|---|
| NVR `/timeline` 範圍大時慢（24h × 4 cam） | 預設 24h 上限；handler 10s timeout |
| NVR 沒回傳 cam 範圍 | 顯示「無資料」+ 提示「該 cam 不存在或 NVR 沒回應」 |
| 8555 沒 NVR 啟用 | 頁面顯示空態 CTA |
| 1440px 在小螢幕溢出 | 容器 `overflow-x: auto`，可橫向卷 |
| 8555 5 天沒重啟 bug 重演 | 寫進 CHANGELOG 提醒「改 web/clips_app.py 必重啟」 |

---

## Success Criteria

- [ ] 12 個新測試全綠
- [ ] 8555 上 `GET /clips/coverage` 回 200、HTML 包含「錄影熱區」
- [ ] 8555 上 `GET /clips/coverage/data?nvr_id=53&start=...&end=...` 回 JSON 含 `cameras[]`
- [ ] Dark toggle、navbar 連結、click-to-jump 邏輯都有
- [ ] 既有 852 個測試不退步
- [ ] 不影響 8444、不動 DB schema
