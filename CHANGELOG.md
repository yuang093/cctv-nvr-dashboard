# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **2026-09-22**（Week 8 released）：最終驗收（Issue #023-#027）— 5 commits，`scripts/docs_factcheck.py`（5 項文件守門員）+ `scripts/check_ci_duration.py`（CI 時間估算）+ 簡化 CI 矩陣為單 Python 3.12（300s → 120s）+ 5 個核心文件同步 + DEPLOY.md 加「雙 App + HTTPS + Audit」段 + `docs/w8-acceptance-report.md` + `docs/roadmap-q4-2026.md`（3 條可選方向：型別 / 即時性 / 多站台）。`pytest -q` 仍維持 1010 全綠 + 0 mypy errors。
- **2026-09-22**（Week 7 released）：mypy strict subset + OpenAPI（Issue #020-#022）— 15 commits（5d75997..9283d8b），51 → 0 mypy errors、1005 → 1010 pytest、45 OpenAPI YAML + 45 `@swag_from`（dashboard 35 + clips 10）、flasgger 0.9.7 + Swagger UI（8444 + 8555 `/apidocs/`）。
- **2026-09-21**（Week 6 released）：Blueprint 拆分（Issue #017-#019）— 9 commits（c8ad63c..516a128），8444 拆 5 個業務領域 bp（dashboard / runs / nvrs / scan / devices）+ 8555 拆 3 個 bp（pages / coverage / media）+ 雙 App 工廠對稱（create_app + create_clips_app）+ alias 攤平機制讓既有 30+ 處 url_for 零修改。
- **2026-09-21**（Week 5 released）：資安提案 B（Issue #012-#016）— 11 commits（b251985..3b67124），Flask-Login + HTTPS（reverse proxy）+ rate-limit（flask-limiter）+ audit_log 表 + NVR 降權。5 個 feature flags 全預設 False，Week 5 結束時 dashboard 行為 = Week 4（內網 ops 完全無感）。
- **2026-08-05 ~ 08-06**（Spec G released）：Cam 健康趨勢圖 — `/trends` 頁面從 snapshot 升級到趨勢判斷（**25 commits**：plan + 4 batches，898 → 974 tests）：
  - **Batch A**（6 commits）：純 DB 查詢層 `web/trends.py`（`HealthBin` / `CamHealthSummary` dataclass + `compute_health_timeseries` 24h/7d + `get_all_cams_health_summary` with ghost/nvr/status filter），13 項純函式測試
  - **Batch B**（8 commits）：`/trends` Flask route + Chart.js 4.4.0 sparkline grid（mini + inline expand detail），peer-review 修法（Critical XSS、filter state preservation、loop.index0 canvas id、theme tokens via CSS var、台北時區、lazy chart init），9 項 route 整合測試 + 3 視覺驗證截圖
  - **Batch C**（6 commits）：4 處 deep-link 入口（navbar「📈 健康趨勢」、devices_list 每 row、dashboard top_missing、coverage 8555 跨 port via `NVR_DASHBOARD_URL` env）+ Task 12 `?cam_id=` auto-expand via `scrollIntoView` + `CSS.escape` + lazy chart 整合。13 項新測試
  - **Batch D**（2 commits）：Spec §12 Public Query Contract + CHANGELOG 收尾 + 5 張視覺驗證截圖（spec-g-batch-c-01..05） + `scripts/seed_visual_demo.py` 灌 sample DB helper
  - **零 schema 改動**（沿用既有 `image_health_checks.metrics_json` 物件欄位代理「健康掃描記錄」）
  - **Spec G §11 勘誤**：`abnormal_bins` 從「bin count」改為「record count 不含 offline」、bin order 從「old→new」改為「new→old」、range query 從 `24|168` int 改為 `24h|7d` string
  - **Spec G §12 Public Query Contract**：所有 deep-link 統一用 `/trends?cam_id=X&range=24h` 形式（公約穩定介面）
  - 重啟提醒：改 `web/app.py` 必重啟 8444 + 改 `web/clips_app.py` 必重啟 8555
  - 規格：`docs/superpowers/specs/2026-08-05-cam-health-trends-design.md`；計畫：`docs/superpowers/plans/2026-08-05-cam-health-trends.md`
- **2026-08-05**：`theme_preview.html` 補 6 個 theme 卡片（user #565，`35c5f45`）—— base.html 在 `036414c` 已補 12 個 theme light link chain，但 `/theme` 頁面只展示 6 張卡片（nordic / brutal / fintech / earthy / editorial / eink），user 沒辦法在 UI 切到 enterprise / glass / gradient / minimal / cyberpunk / terminal。修法：加 6 個新卡片（n7..n12 CSS + 對應 HTML），每張對應其 theme 的設計語彙（enterprise 深藍漸層 / glass 紫粉漸層半透明 / gradient 藍紫漸層 text-clip / minimal 純白黑灰 / cyberpunk 青色霓虹 Orbitron / terminal GitHub dark JetBrains Mono 命令列）+ 2 個 regression test 防日後新增 theme 又漏卡片。視覺驗證：12 卡片齊（037.PNG）；點 Cyberpunk 卡片 → dashboard 套上 cyberpunk 主題（038.PNG，深藍黑底 + 青色霓虹 + Orbitron + 「>」prompt）。895 全綠。
- **2026-08-04**：MockMediaClient 缺 `get_recording_duration`（user 032.PNG，`021fc14`）—— `MediaApiClient` Protocol 沒強制宣告，導致 `MpdMediaClient` 加新方法後 mock 沒實作 → `/clips/fetch_sync` 在 mock 環境下完全壞掉（兩台 cam MPD query 都 AttributeError → NO_COMMON_RECORDING → 前端誤顯示「NVR 連線失敗」）。修法：Protocol 加 method、MockMediaClient 回固定 240s、加 3 個 regression test 防 mock drift。視覺驗證：coverage → 點綠帶 → 1 台自動播放、`/clips` 2×2 同步撥放成功（033/034.PNG）。
- **2026-08-04**：NVR /timeline 範圍查詢 bug (`e145562`) — NVR API 忽略 from/to，回傳視窗外的舊 records。`compute_per_camera_completeness` 內部用 `_clip_to_window` 過濾（所以完整率數字一直對），但 `fetch_coverage_from_nvr` 序列化 records 沒 clip → 前端在視窗外誤繪綠帶。修法：序列化前先 clip 到視窗內，完全在視窗外 drop、邊界重疊裁切。3 新測試（clip / partial overlap / drop outside）+ 864 全綠。
- **2026-08-04**：8444 base.html 補 6 個 light theme 漏鏈 — Spec E 完成時漏掉的 pre-existing bug：enterprise / glass / gradient / minimal / cyberpunk / terminal 在 light 模式沒有對應 CSS 載入（dark 模式已有 *-dark.css）。補 6 個 `{% elif theme == 'X' %}` + 新測試 `test_base_light_theme_links.py` 確保 12 個 theme 都有 light link。51 theme/base tests 全綠。
- **2026-08-04**：user 031.PNG 回饋 — coverage 兩個 bug：
  1. **24h 軸寫死 00-22**（不對應實際查詢視窗）→ 加 `web/coverage.compute_axis_ticks()` 純函式（依查詢視窗動態計算 12 個 tick，台北時區 HH:MM 或跨日 MM-DD HH:MM），`fetch_coverage_from_nvr` 把 ticks 一起回傳給前端。
  2. **點綠帶跳 /clips 後還要選 NVR/時間** → clips.html 加 URL params 自動套用（讀 `?nvr_id` / `?cam_id` / `?t`，自動 select / 勾選 / 填 datetime-local），三者齊全時自動觸發同步撥放。
  - 13 新測試（5 axis_ticks + 8 clips url params 靜態分析）+ 882 全綠。
  - 視覺驗證：查「8/3 下午 03:52 ~ 8/4 下午 03:52」軸顯示 08-03 15:52 → 08-04 15:52（每 ~2.18h 一跳）；點綠帶跳 /clips 自動套用 + 顯示「完成：1 台撥放中」。

### Added
- **2026-08-04**：Spec F 8555 錄影覆蓋熱區 — 多 cam 24h timeline 視覺化。新頁 `/clips/coverage`、新 API `/clips/coverage/data`：
  - 純邏輯 `web/coverage.py`（4 個 public API：parse_records_from_timeline_response / compute_per_camera_completeness / fetch_coverage_from_nvr / CoverageCamera）
  - 獨立頁面 `web/clips_templates/coverage.html`（純 CSS grid 時間軸、點擊區段跳轉 clips 頁、自訂時間範圍、每 cam 完整率）
  - `web/clips_templates/clips.html` + `nvrs_list.html` navbar 加「📼 錄影熱區」連結
  - 17 個新測試（7 純邏輯 + 10 API/頁面/整合）；852 → 869 全綠
  - 5 個 commit：`872652d`(spec)、`9d3ae44`(plan)、`d165111`(Task 1)、`140f89e`+`957bcc9`(Task 2 + 合規修法)、`6bb10b6`(Task 3)、`f3907f7`(Task 4)、`dad3557`(修 2 小漏)
  - **重啟提醒**：改 `web/clips_app.py` 必重啟 8555（無 supervisor）。本功能僅 Port 8555，不影響 8444。

### Added
- **2026-08-03**：021.PNG 藍圖 100% 完成（Spec A / B / B+ / C），共 3 個 commit：
  - `440b6e0` — Spec B：相機健康分布 donut 圖
  - `7e715c2` — Spec B+：雲端覆蓋圖（第二個 donut）
  - `1a5bdb1` — Spec C：本機 gateway 健康 6 指標（Fleet Pulse + 系統狀態，psutil）
- **2026-07-30**：8555 clips app refactor（`258e3f1`）—— Flask Blueprint 把 NVR CRUD 從 8444 抽出到 8555，並加 Dark Mode。35 個新測試。讓 clips app 獨立可用（未來各自打包）。
- **2026-07-30**：NVR dedup 修法（`2fbca94` + `de889fb`）—— `bulk_upsert_nvrs` dedup 完整 migrate OLD NVR 全部資料；`/abnormal` 加 INNER JOIN 過濾 orphan events。
- **2026-07-30**：`/wall` 改進（`5560f79` `33c10b3` `5fec14e`）—— 加「🔄 重新掃描並更新縮圖」按鈕、`batch_scan._IMAGE_HEALTH_ENABLED` 預設開、4 個修法（cam B frozen / cam A 過暗 / cam3 ghost / fetch verbose）。
- **2026-07-30**：`/dashboard` 補（`e3ca96b` `bc29539` `6f094ec`）—— 「🔄 重整完整率」按鈕、ghost 過濾、top missing 排除 disabled NVR。
- **2026-07-30**：GitHub Actions CI（`.github/workflows/ci.yml`）—— push / PR / manual 觸發 Python 3.10/3.11/3.12 全套 703 項測試。
- **2026-07-30**：FROZEN 修法（`63c296d`）—— frozen 不觸發 event 但寫 metrics（修法 N），避免誤報。
- **2026-08-03**：NVR/.gitignore 補強（artifacts、一次性腳本；PNG 截圖保留選擇性 commit）。
- **2026-08-03**：README.md 反映 8444 + 8555 雙 port 架構、28 個 spec 截圖、psutil 依賴。
- **2026-08-03**：8444 / 8555 Design Tokens 統一（11 commits, `04aaf0f`..`4be1ae0`）—— 抽出 `web/static/css/tokens.css` 集中兩套 theme（色票 + 字體 + 間距 + 圓角），8444 `base.html` + 8555 4 個 clips_templates 各自 `<link>` 引入 + `<html data-theme="...">` 切換；`fintech-dark.css` 改寫用 `var()` 引用，152 個 hardcode 顏色全部替換為 token。Spec 在 `docs/superpowers/specs/2026-08-03-design-tokens-unification.md`，計畫在 `docs/superpowers/plans/2026-08-03-design-tokens-unification.md`。預期 744 → 790 測試（實際 790/790 綠）。
- **2026-08-03**：11 主題 dark 補齊（Spec E，12 commits，47 新測試，`5d11cf1`..`694e94a`）—— 為 brutal / cyberpunk / earthy / editorial / eink / enterprise / glass / gradient / minimal / nordic / terminal 各加 `*-dark.css`（fintech-dark 既有），每個保留 light theme 品牌色作 accent（4 個 token：`--primary` / `--primary-hover` / `--text-link` / `--text-link-hover`）。`base.html` 加 12 個 `{% if dark and theme == 'X' %}` link，`*-dark.css` 在 `fintech-dark.css` 之後載入以保最高優先級。Spec 在 `docs/superpowers/specs/2026-08-03-11-themes-dark-coverage.md`，計畫在 `docs/superpowers/plans/2026-08-03-11-themes-dark-coverage.md`。預期 805 → 853 測試（實際 852 綠，差 1 為 fintech-dark 既有複用）。

### Fixed
- **2026-08-03**：`list_cameras_for_nvr` 漏過濾 ghost cam（`b6a36a8`）—— 8555 clip UI 取 NVR cam list 會 404，已補 `is_ghost = 0` 過濾。
- **2026-07-30**：`/wall` 縮圖永遠是 placeholder 的 bug（`33c10b3`）—— `batch_scan._IMAGE_HEALTH_ENABLED` 預設為 `False`，已改 env 預設為 "1"。

### Known Issues
- **2026-08-03**：`web/templates/base.html` lines 14-26 light theme link chain 缺 6 個 theme（enterprise / glass / gradient / minimal / cyberpunk / terminal），這 6 個 theme 在 light 模式下沒有對應 CSS 載入（dark 模式已有 `*-dark.css`）。Spec E scope 為補 dark，未修 light 漏鏈。下一輪 Task 14 應補。

---

## [2026-07-30 之前]
- 規劃文件（README / overview / api_endpoints / class_interface / database_schema）
- `nvr_config.json` 多 NVR 設定範本
- `AvigilonScanner` class（含 SHA-256 auth）
- `SqliteWriter`（`IDatabaseWriter` 實作）
- `batch_scan()` 多 NVR 入口
- pytest 測試 132 項 → 703 項演進
- 部署指南 + 入口腳本
- v2 Web UI 雛形（Flask 5 routes + 6 templates）
- 整合測試（MockAvigilonServer + MockWebhookReceiver）
- Webhook 推播（Slack / Teams 異常通知）
- Phase 1：事件 resolved 追蹤
- Phase 2.7：影片片段調閱（MediaApiClient Protocol + MockClient + clips Flask app port 8555 + 兩段式 UX）
- Phase 2.7 補：NVR 連線失敗追蹤（`nvr_failure_log` 表 + `log_nvr_failure()`）
- Phase 2.7 補：DB-as-source-of-truth（`nvr_servers.enabled` 欄位 + `list_enabled_nvrs()` + `set_nvr_enabled()`）
