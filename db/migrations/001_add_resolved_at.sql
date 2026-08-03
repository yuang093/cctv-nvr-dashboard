-- 001_add_resolved_at.sql
-- ========================
-- Phase 1 事件 resolved 追蹤：為 events 表加 resolved_at 欄位。
--
-- Purpose:
--   events 表原本只記錄「事件發生」，加上 resolved_at 後可記錄「事件被解決」，
--   NULL 表示事件進行中、非 NULL 表示已恢復（UTC ISO 8601 字串）。
--
-- Usage:
--   用 db/sqlite_writer.py 啟動時自動跑（無需手動執行）。
--   若需離線 migrate：執行 db/migrations/migrate_add_resolved_at.py
--
-- Idempotency:
--   SQLite 不支援「ADD COLUMN IF NOT EXISTS」直到 3.35+
--   python sqlite_writer.py 內已用 PRAGMA table_info 檢查存在後才 ALTER，
--   重複執行是 safe no-op。

ALTER TABLE events ADD COLUMN resolved_at TEXT;
