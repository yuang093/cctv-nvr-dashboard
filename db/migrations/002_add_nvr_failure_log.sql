-- 002_add_nvr_failure_log.sql
-- ===========================
-- NVR 連線失敗記錄表：當 batch_scan 中某台 NVR 連線失敗時，
-- 寫一筆到這張表，給 dashboard / run_detail / webhook 統計用。
--
-- 跟 scan_runs.total_nvrs / failed_nvrs 數字不同：這張表記「個別 NVR 失敗原因」。
--
-- Usage:
--   用 db/sqlite_writer.py 啟動時自動跑（無需手動執行）。
--   若需離線 migrate：執行 db/migrations/migrate_add_nvr_failure_log.py
--
-- Idempotency:
--   用 PRAGMA table_info 檢查存在後才 CREATE，SqliteWriter 內實作。
--   重複執行是 safe no-op。

CREATE TABLE IF NOT EXISTS nvr_failure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
    nvr_id TEXT NOT NULL,                -- NVR 設定檔的 id（例 "NVR-A"）
    nvr_name TEXT NOT NULL,              -- 顯示名稱（snapshot — 不 JOIN 設定檔）
    nvr_internal_id INTEGER,             -- nvr_servers.id（nullable：upsert 失敗時無 internal id）
    error_type TEXT NOT NULL,            -- 例 "ConnectionError" / "Timeout" / "AuthError"
    error_message TEXT NOT NULL,         -- 完整錯誤訊息
    failed_at TEXT NOT NULL              -- ISO8601 UTC（例 "2026-07-07T09:00:00Z"）
);

CREATE INDEX IF NOT EXISTS idx_nvr_failure_log_scan_run_id
    ON nvr_failure_log(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_nvr_failure_log_nvr_id_failed_at
    ON nvr_failure_log(nvr_id, failed_at DESC);