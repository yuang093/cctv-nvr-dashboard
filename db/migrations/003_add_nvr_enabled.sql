-- 003_add_nvr_enabled.sql
-- ========================
-- Phase 2.7+ 起：NVR 啟用/停用由 DB 管理，不再依賴 nvr_config.json。
-- 為 nvr_servers 表加 enabled 欄位；舊 DB 預設全啟用（1）。
--
-- Usage:
--   用 db/sqlite_writer.py 啟動時自動跑（無需手動執行）。
--   若需離線 migrate：執行 db/migrations/migrate_add_nvr_enabled.py
--
-- Idempotency:
--   SQLite 不支援「ADD COLUMN IF NOT EXISTS」直到 3.35+
--   python sqlite_writer.py 內已用 PRAGMA table_info 檢查存在後才 ALTER，
--   重複執行是 safe no-op。

ALTER TABLE nvr_servers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;