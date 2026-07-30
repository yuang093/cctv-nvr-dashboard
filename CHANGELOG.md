# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]
### Added
- 建立專案基礎文件 (`README.md`, `overview.md`, `api_endpoints.md` 等)。
- 確立採用 Avigilon REST API (架構二) 進行全域狀態掃描之核心邏輯。
- GitHub Actions CI (`.github/workflows/ci.yml`) — push / PR / manual 觸發 Python 3.10/3.11/3.12 全套 132 項測試。

### Fixed
- 2026-07-30 `/wall` 縮圖永遠是 placeholder 的 bug。
  - **根因**：`batch_scan._IMAGE_HEALTH_ENABLED` 預設為 `False`（env `NVR_IMAGE_HEALTH` 未設或 != "1"），導致 Web UI `/scan` 觸發的 batch_scan 不會跑 image_health_loop → `camera_snapshots` 永遠沒資料。
  - **修法**：env 預設改為 "1"，且條件改成 `!= "0"`。未設 → 開；`=0` → 關（保留 opt-out）。`/wall` 縮圖從此跟 /scan 同步。
  - **注意**：`batch_scan.py` 目前仍在 `git status` untracked（7/29 起就在用，但未入 git）；本 fix 的源碼改動在 `batch_scan.py`，commit `33c10b3` 只 track 了對應測試。下次整理 repo 時一併補 commit。
  - **測試**：`tests/test_image_health_default_on.py`（5 個）。完整 suite 701/701 全綠（164.89s）。