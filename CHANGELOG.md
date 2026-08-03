# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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

### Fixed
- **2026-08-03**：`list_cameras_for_nvr` 漏過濾 ghost cam（`b6a36a8`）—— 8555 clip UI 取 NVR cam list 會 404，已補 `is_ghost = 0` 過濾。
- **2026-07-30**：`/wall` 縮圖永遠是 placeholder 的 bug（`33c10b3`）—— `batch_scan._IMAGE_HEALTH_ENABLED` 預設為 `False`，已改 env 預設為 "1"。

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
