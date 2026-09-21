"""
db/sqlite_writer.py
====================
SQLite 寫入層：實作 `class_interface.md` 中的 IDatabaseWriter Protocol。

設計重點：
    - 零外部依賴（僅用 Python 內建 sqlite3）
    - 每個 scan_run 為一個 transaction（begin → 操作 → finish commit）
    - 異常時自動 rollback（finally 中 close 不 commit）
    - Schema 自動建立（首次 init_schema 時）

使用範例：
    writer = SqliteWriter("./nvr_scan.db")
    nvr_id = writer.upsert_nvr(nvr_config)
    run_id = writer.begin_scan_run("2026-06-23T10:00:00Z")
    writer.upsert_cameras(nvr_id, cameras_dict)
    writer.insert_events(run_id, nvr_id, events_list)
    writer.finish_scan_run(run_id, finished_at=..., status="success", stats={...})
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Schema 完整 SQL（從 database_schema.md 對齊）
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nvr_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nvr_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 8443,
    username TEXT,
    password TEXT,
    verify_ssl INTEGER NOT NULL DEFAULT 0,
    site_id TEXT,
    tags TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cameras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
    device_id TEXT NOT NULL,
    camera_name TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip_address TEXT,
    mac_address TEXT,
    is_ghost INTEGER NOT NULL DEFAULT 0,
    UNIQUE (nvr_id, device_id)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    total_nvrs INTEGER NOT NULL DEFAULT 0,
    ok_nvrs INTEGER NOT NULL DEFAULT 0,
    failed_nvrs INTEGER NOT NULL DEFAULT 0,
    total_cameras INTEGER NOT NULL DEFAULT 0,
    abnormal_cameras INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
    nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
    camera_id INTEGER REFERENCES cameras(id),
    event_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    event_topic TEXT NOT NULL,
    event_topics_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    raw_json TEXT NOT NULL
);

-- Week 3 Issue #008：原本這裡有 3 個 CREATE INDEX ON events(...)，
-- 但 events 已被 migration 轉為 view（SQLite 不支援 view 上的 index）。
-- 索引已改由 db.sqlite_writer._migrate_add_events_partition 建在當月 monthly table。
-- 既有 legacy DB 的索引會留在 events_legacy 上（orphaned but harmless，純 rollback 用途）。

-- Phase 2.8（Arisan 影像健康巡檢）：每張 cam 縮圖分析紀錄。
CREATE TABLE IF NOT EXISTS image_health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    nvr_server_id INTEGER REFERENCES nvr_servers(id),
    checked_at_utc TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    flags_json TEXT NOT NULL,
    triggered_event_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_health_cam_time
    ON image_health_checks(camera_id, checked_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_health_nvr
    ON image_health_checks(nvr_server_id);

-- Phase 2.8（Arisan 探索網段）：每個探索任務一筆紀錄。
CREATE TABLE IF NOT EXISTS discover_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    cidr TEXT NOT NULL,
    port INTEGER DEFAULT 8443,
    results_json TEXT NOT NULL,
    status TEXT NOT NULL
);

-- Phase 2.8（Arisan 17 種故障類型統一顯示）：UI 一律顯示中文 catalog。
CREATE TABLE IF NOT EXISTS event_kind_catalog (
    event_topic TEXT PRIMARY KEY,
    name_zh TEXT NOT NULL,
    name_en TEXT NOT NULL,
    category TEXT NOT NULL,
    is_fault INTEGER NOT NULL,
    sort_order INTEGER
);

-- Phase 2.8（Arisan 錄影完整率）：每台 cam 最近一次的 24h 完整率 + 缺段秒數。
CREATE TABLE IF NOT EXISTS recording_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
    camera_id TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    completeness REAL NOT NULL,
    missing_seconds REAL NOT NULL,
    checked_at TEXT NOT NULL,
    UNIQUE (nvr_id, camera_id)
);
CREATE INDEX IF NOT EXISTS idx_recording_status_checked_at
    ON recording_status(checked_at DESC);
CREATE TABLE IF NOT EXISTS camera_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
    camera_id TEXT NOT NULL,
    jpeg_bytes BLOB NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE (nvr_id, camera_id)
);
CREATE INDEX IF NOT EXISTS idx_camera_snapshots_captured_at
    ON camera_snapshots(captured_at DESC);
"""


def _now_utc_iso() -> str:
    """取得當下 UTC 時間，ISO 8601 格式。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_camera_name(info: Any) -> str:
    """相容處理新舊兩種 cameras dict 值：純字串 或 含 name 的 dict。"""
    if isinstance(info, dict):
        return str(info.get("name") or info.get("deviceId") or "?")
    return str(info)


def _coerce_camera_field(info: Any, key: str) -> str | None:
    """從 cameras dict 抓可選欄位（ip_address / mac_address 等）。

    純字串（舊格式）視為無此欄位 → None。
    dict 內缺值或空字串也 → None（避免寫入垃圾資料）。
    """
    if not isinstance(info, dict):
        return None
    val = info.get(key)
    if val is None or val == "":
        return None
    return str(val)


class SqliteWriter:
    """
    SQLite 寫入層，實作 IDatabaseWriter Protocol（見 class_interface.md）。

    使用方式（per-scan-run lifecycle）：
        writer = SqliteWriter(db_path)
        nvr_id = writer.upsert_nvr(nvr_config)
        run_id = writer.begin_scan_run(started_at)
        writer.upsert_cameras(nvr_id, cameras)
        writer.insert_events(run_id, nvr_id, events)
        writer.finish_scan_run(run_id, finished_at=..., status="success", stats=...)

    一個 SqliteWriter instance 同時只維護一個 active scan_run transaction。
    若未 finish 就丟例外，transaction 會自動 rollback（connection close 不 commit）。
    """

    def __init__(self, db_path: str):
        """
        Args:
            db_path: SQLite 檔案路徑。":memory:" 用於測試。
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._current_scan_run_id: int | None = None
        # 確保 parent dir 存在（除 :memory: 外）
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # 一次性初始化 schema
        self._init_schema()

    # --- 內部 ---
    def _init_schema(self) -> None:
        """建立所有 table / index（若不存在）+ 對舊 DB 跑 migration。"""
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        # Phase 1 起的 idempotent migration：對 v1 schema 的 events 表補 resolved_at
        self._migrate_add_resolved_at(conn)
        # Phase 2.7+ 起：個別 NVR 連線失敗記錄表
        self._migrate_add_nvr_failure_log(conn)
        # Phase 2.7+ 起：NVR 啟用狀態改由 DB 管理（取代 nvr_config.json）
        self._migrate_add_nvr_enabled(conn)
        # Phase 2.8（Arisan）：影像健康、探索網段、事件 catalog、cameras 欄位
        self._migrate_add_cameras_last_health_check_id(conn)
        self._migrate_add_cameras_network(conn)
        self._migrate_add_discover_sessions_port(conn)
        self._migrate_seed_event_kind_catalog(conn)
        self._migrate_add_cameras_is_ghost(conn)
        # Week 3 Issue #008：events 月分區。必須在以下兩個條件都滿足之後：
        #   1. _migrate_add_resolved_at 已跑完（補 resolved_at 欄位到 events 表），
        #      否則 partition migration 的 `INSERT INTO new SELECT * FROM legacy`
        #      會因欄位數不一致（legacy 11 / new 12）而違反 raw_json NOT NULL。
        #   2. uq_events_open_per_topic index 之前（SQLite 不支援 view 上的 index，
        #      要等 migration 把 events 轉成 view + monthly table 後才能建在 monthly）。
        self._migrate_add_events_partition(conn)
        # Week 4 Issue #011：events view 改為動態 UNION hot tables（90 天滑動窗）。
        # 必須在 partition migration 之後（view 已建立）才能 rebuild。
        if self._is_events_view(conn):
            self._migrate_rebuild_events_view_union(conn)
        # Week 5 Issue #015：audit_log 表（idempotent，預設不寫入由 flag 控制）。
        self._migrate_create_audit_log(conn)
        # Week 5 Issue #012：users 表 + 預設 admin（idempotent）。
        self._migrate_create_users(conn)
        # Partial UNIQUE index：跨 process 避免重複寫入 open event。
        # 若 events 已被 migration 轉成 view（SQLite 不支援 view 上的 index），
        # index 已由 migration 建在當月 monthly table 上；此處跳過。
        if not self._is_events_view(conn):
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_events_open_per_topic
                ON events(nvr_id, device_id, event_topic) WHERE resolved_at IS NULL
                """
            )
        conn.commit()

    @staticmethod
    def _is_events_view(conn: sqlite3.Connection) -> bool:
        """Week 3 Issue #008：偵測 events 是否已被 migration 轉為 view。"""
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='events'"
        ).fetchone()
        return row is not None and row["type"] == "view"

    @staticmethod
    def _migrate_add_events_partition(conn: sqlite3.Connection) -> None:
        """Week 3 Issue #008：events 表 → 月分區 + view + INSTEAD OF triggers（idempotent）。

        策略：
          a. 偵測 events 是否已是 view（已是 → skip）
          b. 偵測是否已有當月 events_YYYY_MM 表（已是 → skip）
          c. 既有 events 表 → rename 為 events_legacy（資料保留供 rollback）
          d. 建立當月 events_YYYY_MM 表 + 複製 legacy 資料
          e. 建立 events view = 只看當月表
          f. INSTEAD OF INSERT / UPDATE triggers 自動路由寫入
          g. Partial UNIQUE INDEX（綁 monthly table）

        對應獨立 offline 腳本：db/migrations/migrate_add_events_partition.py
        """
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='events'"
        ).fetchone()
        if row is None:
            return  # 沒有 events 表（還沒初始化；SCHEMA_SQL 會建）
        if row["type"] == "view":
            return  # 已是 view，重跑略過

        # 計算當月分區表名（與 offline 腳本邏輯一致）
        now = datetime.now(timezone.utc)
        current_month = f"events_{now.year:04d}_{now.month:02d}"

        # 檢查當月表是否已存在（保守起見）
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (current_month,)
        ).fetchone()
        if existing:
            return

        # c. rename 既有 events → events_legacy
        conn.execute("ALTER TABLE events RENAME TO events_legacy")

        # d. 建立當月表（同 schema，含 camera_id；對應 database_schema.md §4 共 12 欄）
        conn.execute(
            f"""
            CREATE TABLE {current_month} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_run_id INTEGER NOT NULL,
                nvr_id INTEGER NOT NULL,
                camera_id INTEGER NULL,
                event_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                event_topic TEXT NOT NULL,
                event_topics_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                resolved_at TEXT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )

        # 複製 legacy 資料進當月表（用顯式欄位名，避免 ALTER TABLE ADD COLUMN
        # 把欄位加到尾端造成的 position mismatch）。
        # 舊 v1 events 表可能缺 resolved_at；用 COALESCE 容錯（沒欄位時 SELECT 對
        # resolved_at 拋錯，但這種情況已被 _migrate_add_resolved_at 提前處理過）。
        conn.execute(
            f"""
            INSERT INTO {current_month} (
                id, scan_run_id, nvr_id, camera_id, event_id, device_id,
                event_topic, event_topics_json, occurred_at, detected_at,
                resolved_at, raw_json
            )
            SELECT
                id, scan_run_id, nvr_id, camera_id, event_id, device_id,
                event_topic, event_topics_json, occurred_at, detected_at,
                resolved_at, raw_json
            FROM events_legacy
            """
        )

        # e. 建立 events view（只看當月表；legacy 保留資料供 emergency rollback）
        conn.execute(
            f"CREATE VIEW events AS SELECT * FROM {current_month}"
        )

        # f. INSTEAD OF INSERT trigger（簡化版寫死當月表；12 欄對應 schema）
        conn.execute(
            f"""
            CREATE TRIGGER events_insert_router
            INSTEAD OF INSERT ON events
            FOR EACH ROW
            BEGIN
                INSERT INTO {current_month}
                VALUES (NEW.id, NEW.scan_run_id, NEW.nvr_id, NEW.camera_id,
                        NEW.event_id, NEW.device_id, NEW.event_topic,
                        NEW.event_topics_json, NEW.occurred_at, NEW.detected_at,
                        NEW.resolved_at, NEW.raw_json);
            END
            """
        )

        # INSTEAD OF UPDATE trigger（依 id 找對應月份表更新；含 bulk UPDATE）
        conn.execute(
            f"""
            CREATE TRIGGER events_update_router
            INSTEAD OF UPDATE ON events
            FOR EACH ROW
            BEGIN
                UPDATE {current_month}
                SET scan_run_id = NEW.scan_run_id,
                    nvr_id = NEW.nvr_id,
                    event_id = NEW.event_id,
                    device_id = NEW.device_id,
                    event_topic = NEW.event_topic,
                    event_topics_json = NEW.event_topics_json,
                    occurred_at = NEW.occurred_at,
                    detected_at = NEW.detected_at,
                    resolved_at = NEW.resolved_at,
                    raw_json = NEW.raw_json
                WHERE id = OLD.id;
            END
            """
        )

        # g. Partial UNIQUE INDEX（綁 monthly table；SQLite 不支援 view 上的 index）
        conn.execute(
            f"""
            CREATE UNIQUE INDEX uq_events_open_per_topic
            ON {current_month}(nvr_id, device_id, event_topic)
            WHERE resolved_at IS NULL
            """
        )

        # h. 對應原本 SCHEMA_SQL 的 3 個 events 索引（Week 3 Issue #008 移到 migration 內）
        conn.execute(
            f"CREATE INDEX idx_events_scan_run_id ON {current_month}(scan_run_id)"
        )
        conn.execute(
            f"CREATE INDEX idx_events_nvr_occurred ON {current_month}(nvr_id, occurred_at)"
        )
        conn.execute(
            f"CREATE INDEX idx_events_detected_at ON {current_month}(detected_at)"
        )

    @staticmethod
    def _migrate_rebuild_events_view_union(conn: sqlite3.Connection) -> None:
        """Week 4 Issue #011：events view 動態 UNION 4 張熱表（idempotent）。

        委派給 db.event_partition.rebuild_events_view 統一處理 view + triggers 重建。
        """
        from db.event_partition import rebuild_events_view

        rebuild_events_view(conn, hot_window=4)

    @staticmethod
    def _migrate_create_audit_log(conn: sqlite3.Connection) -> None:
        """Week 5 Issue #015：建立 audit_log 表（idempotent）。"""
        from db.migrations.migrate_create_audit_log import SCHEMA

        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if existing:
            return
        # schema_migrations 表可能不存在（首次 init 全新 DB）→ 先確保它存在
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.executescript(SCHEMA)
        from datetime import datetime, timezone

        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (6, datetime.now(timezone.utc).isoformat()),
        )

    @staticmethod
    def _migrate_create_users(conn: sqlite3.Connection) -> None:
        """Week 5 Issue #012：建立 users 表 + 預設 admin（idempotent）。

        注意：admin 帳號只在表完全不存在時才建立（避免覆蓋既有密碼）。
        """
        from db.migrations.migrate_create_users import SCHEMA

        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if existing:
            return  # 表已存在；admin 帳號保留不動

        from datetime import datetime, timezone

        # 確保 schema_migrations 存在
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (7, datetime.now(timezone.utc).isoformat()),
        )

    @staticmethod
    def _migrate_add_resolved_at(conn: sqlite3.Connection) -> None:
        """若 events 表缺 resolved_at 欄位 → ALTER TABLE（idempotent）。"""
        cols = [
            row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()
        ]
        if "resolved_at" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN resolved_at TEXT")

    @staticmethod
    def _migrate_add_nvr_failure_log(conn: sqlite3.Connection) -> None:
        """建立 nvr_failure_log 表 + 索引（idempotent）。

        v2.7+ 起，個別 NVR 連線失敗會寫入這張表（不只是 count 數字）。
        用 CREATE TABLE IF NOT EXISTS 確保 idempotent。
        """
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nvr_failure_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
                nvr_id TEXT NOT NULL,
                nvr_name TEXT NOT NULL,
                nvr_internal_id INTEGER,
                error_type TEXT NOT NULL,
                error_message TEXT NOT NULL,
                failed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_nvr_failure_log_scan_run_id
                ON nvr_failure_log(scan_run_id);
            CREATE INDEX IF NOT EXISTS idx_nvr_failure_log_nvr_id_failed_at
                ON nvr_failure_log(nvr_id, failed_at DESC);
        """)

    @staticmethod
    def _migrate_add_nvr_enabled(conn: sqlite3.Connection) -> None:
        """為 nvr_servers 加 enabled 欄位（idempotent）。

        v2.7+ 起，NVR 啟用狀態由 DB 管理（取代 nvr_config.json 的 enabled 過濾）。
        舊 DB 預設全啟用（DEFAULT 1）。
        """
        cols = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(nvr_servers)").fetchall()
        ]
        if "enabled" not in cols:
            conn.execute(
                "ALTER TABLE nvr_servers "
                "ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
            )

    @staticmethod
    def _migrate_add_cameras_last_health_check_id(conn: sqlite3.Connection) -> None:
        """Phase 2.8（Arisan 影像健康巡檢）：為 cameras 加 last_health_check_id 欄位（idempotent）。

        反向指向最近一次 image_health_checks 紀錄，方便 dashboard「這台 cam 最後檢查時間」。
        Nullable（尚未檢查過的 cam 為 NULL）。
        """
        cols = [
            row["name"] for row in conn.execute("PRAGMA table_info(cameras)").fetchall()
        ]
        if "last_health_check_id" not in cols:
            conn.execute("ALTER TABLE cameras ADD COLUMN last_health_check_id INTEGER")

    @staticmethod
    def _migrate_add_cameras_network(conn: sqlite3.Connection) -> None:
        """Phase 2.8（Arisan）：為 cameras 加 ip_address / mac_address 欄位（idempotent）。

        ip_address 來自 NVR /cameras API 的 ipAddress 欄位（格式通常 ip:port）。
        mac_address 來自 physicalAddress。
        Nullable（舊 NVR API 沒回、或 NVR 未跑過新版本 writer 就不填）。
        """
        cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(cameras)").fetchall()
        }
        if "ip_address" not in cols:
            conn.execute("ALTER TABLE cameras ADD COLUMN ip_address TEXT")
        if "mac_address" not in cols:
            conn.execute("ALTER TABLE cameras ADD COLUMN mac_address TEXT")

    @staticmethod
    def _migrate_add_cameras_is_ghost(conn: sqlite3.Connection) -> None:
        """2026-07-30：cam ghost 標記。NVR 重啟時可能短暫看到 RTSP stream placeholder，
        之後消失；DB 還有但 NVR 不再報。`is_ghost = 1` 表示「目前 NVR 未管理」。

        預設 0（活躍）。scan 結束時 batch_scan 呼叫 `mark_ghost_cameras()` 維護。
        """
        cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(cameras)").fetchall()
        }
        if "is_ghost" not in cols:
            conn.execute(
                "ALTER TABLE cameras ADD COLUMN is_ghost INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _migrate_add_discover_sessions_port(conn: sqlite3.Connection) -> None:
        """Phase 2.8（Arisan 探索網段 Phase #6）：為 discover_sessions 加 port 欄位（idempotent）。

        Phase #6 才知道每個 session probe 哪個 port（v1 全用 8443 預設）。
        Nullable 即可；缺值時 web.db.get_session_port() fallback 8443。
        """
        cols = [
            row["name"]
            for row in conn.execute("PRAGMA table_info(discover_sessions)").fetchall()
        ]
        if "port" not in cols:
            conn.execute(
                "ALTER TABLE discover_sessions ADD COLUMN port INTEGER DEFAULT 8443"
            )

    @staticmethod
    def _migrate_seed_event_kind_catalog(conn: sqlite3.Connection) -> None:
        """Phase 2.8（Arisan 17 種故障類型）：seed event_kind_catalog。

        對齊 user 提供的 17 種對照表（8 DEVICE_* + 9 STATE_*）。
        使用 INSERT OR IGNORE → 已存在的 topic 不覆寫（保留日後手動補的翻譯）。
        """
        rows = [
            # (event_topic, name_zh, name_en, category, is_fault, sort_order)
            (
                "DEVICE_VIDEO_SIGNAL_LOST",
                "影像訊號斷線（黑畫面）",
                "Video signal lost (black screen)",
                "DEVICE",
                1,
                10,
            ),
            (
                "DEVICE_TAMPERING",
                "破壞/遮蔽（場景改變）",
                "Tampering / scene changed",
                "DEVICE",
                1,
                20,
            ),
            (
                "DEVICE_COMMUNICATION_LOST",
                "通訊中斷",
                "Communication lost",
                "DEVICE",
                1,
                30,
            ),
            (
                "DEVICE_CONNECTION_ERROR",
                "連線錯誤",
                "Connection error",
                "DEVICE",
                1,
                40,
            ),
            (
                "DEVICE_LONG_FAILED",
                "長期失敗（拔線）",
                "Long failed (unplugged)",
                "DEVICE",
                1,
                50,
            ),
            ("DEVICE_DISCONNECTED", "斷線", "Disconnected", "DEVICE", 1, 60),
            (
                "DEVICE_ANOMALY_START",
                "影像分析異常",
                "Video analytics anomaly",
                "DEVICE",
                1,
                70,
            ),
            (
                "DEVICE_UNUSUAL_STARTED",
                "未預期活動",
                "Unusual activity started",
                "DEVICE",
                1,
                80,
            ),
            (
                "STATE_DISCONNECTED",
                "斷線（攝影機無回應）",
                "State: disconnected",
                "STATE",
                1,
                110,
            ),
            (
                "STATE_NOT_RESPONDING",
                "無回應（攝影機 hang）",
                "State: not responding",
                "STATE",
                1,
                120,
            ),
            ("STATE_FAILED", "連線失敗", "State: failed", "STATE", 1, 130),
            (
                "STATE_LONG_FAILED",
                "長期失敗（拔網路線）",
                "State: long failed",
                "STATE",
                1,
                140,
            ),
            (
                "STATE_BAD_CERTIFICATE",
                "憑證錯誤",
                "State: bad certificate",
                "STATE",
                1,
                150,
            ),
            (
                "STATE_AUTH_FAILED",
                "認證失敗（帳密錯）",
                "State: auth failed",
                "STATE",
                1,
                160,
            ),
            ("STATE_NETWORK_DOWN", "網路斷線", "State: network down", "STATE", 1, 170),
            ("STATE_TIMED_OUT", "連線逾時", "State: timed out", "STATE", 1, 180),
            # STATE_CONNECTING 是資訊性事件（短暫狀態），不計入 pending 警示
            (
                "STATE_CONNECTING",
                "連線中（短暫狀態）",
                "State: connecting (transient)",
                "STATE",
                0,
                200,
            ),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO event_kind_catalog
                (event_topic, name_zh, name_en, category, is_fault, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _get_conn(self) -> sqlite3.Connection:
        """取得進行中 transaction 的 connection（lazy 建立）。"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
            # WAL 模式：reader 不會被 writer block（背景 worker + Web UI 並行安全）
            self._conn.execute("PRAGMA journal_mode = WAL")
            # busy_timeout：遇到 lock 時等待最多 30s 而非立即 raise
            self._conn.execute("PRAGMA busy_timeout = 30000")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        return self._conn

    def _require_active(self) -> sqlite3.Connection:
        """確認有 active scan_run，否則拋 RuntimeError。"""
        if self._conn is None or self._current_scan_run_id is None:
            raise RuntimeError("沒有進行中的 scan_run，請先呼叫 begin_scan_run()")
        return self._conn

    # --- IDatabaseWriter Protocol ---

    def upsert_nvr(self, nvr_config: dict) -> int:
        """
        新增或更新 NVR 設定（鏡像 nvr_config.json），回傳內部主鍵 ID。

        若 `nvr_id` 已存在則更新 name/host/port/...；不存在則 INSERT。
        created_at 用既有值（若有），updated_at 永遠更新為當下。

        Args:
            nvr_config: 來自 nvr_config.json 的單台 NVR 設定。

        Returns:
            內部主鍵（INTEGER，可作為 FK 傳給 upsert_cameras / insert_events）。
        """
        now = _now_utc_iso()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO nvr_servers (
                nvr_id, name, host, port, username, password,
                verify_ssl, site_id, tags, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nvr_id) DO UPDATE SET
                name = excluded.name,
                host = excluded.host,
                port = excluded.port,
                username = excluded.username,
                password = excluded.password,
                verify_ssl = excluded.verify_ssl,
                site_id = excluded.site_id,
                tags = excluded.tags,
                updated_at = excluded.updated_at
            """,
            (
                str(nvr_config["id"]),
                str(nvr_config.get("name", nvr_config["id"])),
                str(nvr_config["host"]),
                int(nvr_config.get("port", 8443)),
                nvr_config.get("username"),
                nvr_config.get("password"),
                1 if nvr_config.get("verify_ssl", False) else 0,
                nvr_config.get("site_id"),
                json.dumps(nvr_config.get("tags", []), ensure_ascii=False),
                now,  # 新增時的 created_at
                now,  # updated_at 永遠為當下
            ),
        )
        conn.commit()
        # 重新查詢以取得 ID（不論是 INSERT 還是 UPDATE 都要）
        row = conn.execute(
            "SELECT id FROM nvr_servers WHERE nvr_id = ?",
            (str(nvr_config["id"]),),
        ).fetchone()
        return int(row["id"])

    def begin_scan_run(self, started_at: str) -> int:
        """
        開始一次 scan_run，開啟 transaction。

        Args:
            started_at: ISO 8601 UTC 字串。

        Returns:
            scan_run_id（內部主鍵）。

        Raises:
            RuntimeError: 已有 active scan_run 未結束。
        """
        if self._current_scan_run_id is not None:
            raise RuntimeError("已有進行中的 scan_run，請先呼叫 finish_scan_run()")
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO scan_runs (
                started_at, status, total_nvrs, ok_nvrs, failed_nvrs,
                total_cameras, abnormal_cameras
            ) VALUES (?, 'running', 0, 0, 0, 0, 0)
            """,
            (started_at,),
        )
        self._current_scan_run_id = int(cur.lastrowid)
        return self._current_scan_run_id

    def upsert_cameras(self, nvr_id: int, cameras: dict[str, Any]) -> None:
        """
        新增或更新 cameras（同一 nvr_id + device_id 不重複）。

        Args:
            nvr_id: nvr_servers.id（從 upsert_nvr 取得）。
            cameras: {deviceId: name} 或 {deviceId: {"name": ..., "connection_state": ..., ...}}。

        Raises:
            RuntimeError: 無 active scan_run。
        """
        conn = self._require_active()
        now = _now_utc_iso()
        for dev_id, info in cameras.items():
            name = _coerce_camera_name(info)
            ip = _coerce_camera_field(info, "ip_address")
            mac = _coerce_camera_field(info, "mac_address")
            conn.execute(
                """
                INSERT INTO cameras (nvr_id, device_id, camera_name, last_seen_at,
                                     ip_address, mac_address, is_ghost)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(nvr_id, device_id) DO UPDATE SET
                    camera_name = excluded.camera_name,
                    last_seen_at = excluded.last_seen_at,
                    ip_address = excluded.ip_address,
                    mac_address = excluded.mac_address,
                    is_ghost = 0
                """,
                (nvr_id, str(dev_id), name, now, ip, mac),
            )

    def mark_ghost_cameras(self, nvr_id: int, active_device_ids: list[str]) -> None:
        """2026-07-30：把這次 scan 沒看到的 cam 標 is_ghost = 1。

        NVR 重啟時可能短暫看到 RTSP stream placeholder（名稱顯示為 `rtsp://...`），
        下次 scan 消失。DB 內 cam 仍存在 → /wall 仍會顯示為 placeholder。

        行為：
          - active_device_ids 內的 cam → is_ghost = 0（活躍）
          - 其他 cam（DB 內但沒出現在本次 scan）→ is_ghost = 1（ghost）

        在 `upsert_cameras()` 之後呼叫（這樣剛 upsert 的 cam 是 0，
        沒 upsert 的舊 cam 才會被標 1）。

        Args:
            nvr_id: nvr_servers.id。
            active_device_ids: 這次 scan 看到的 device_id list。

        Raises:
            RuntimeError: 無 active scan_run。
        """
        conn = self._require_active()
        # 已活躍的 → is_ghost = 0
        if active_device_ids:
            placeholders = ",".join("?" * len(active_device_ids))
            conn.execute(
                f"UPDATE cameras SET is_ghost = 0 WHERE nvr_id = ? "
                f"AND device_id IN ({placeholders})",
                [nvr_id, *active_device_ids],
            )
        # 其餘的 → is_ghost = 1
        if active_device_ids:
            placeholders = ",".join("?" * len(active_device_ids))
            conn.execute(
                f"UPDATE cameras SET is_ghost = 1 WHERE nvr_id = ? "
                f"AND device_id NOT IN ({placeholders})",
                [nvr_id, *active_device_ids],
            )
        else:
            conn.execute(
                "UPDATE cameras SET is_ghost = 1 WHERE nvr_id = ?",
                (nvr_id,),
            )

    def upsert_recording_status(
        self,
        nvr_id: int,
        camera_id: str,
        *,
        window_start: str,
        window_end: str,
        completeness: float,
        missing_seconds: float,
    ) -> None:
        """Phase 2.8（Arisan 錄影完整率）：寫/更新單台 cam 的 24h 完整率 + 缺段秒數。

        同一 (nvr_id, camera_id) 第二次呼叫會覆蓋（最新的 window + 指標勝出）。
        """
        conn = self._require_active()
        now = _now_utc_iso()
        conn.execute(
            """
            INSERT INTO recording_status
                (nvr_id, camera_id, window_start, window_end,
                 completeness, missing_seconds, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nvr_id, camera_id) DO UPDATE SET
                window_start = excluded.window_start,
                window_end = excluded.window_end,
                completeness = excluded.completeness,
                missing_seconds = excluded.missing_seconds,
                checked_at = excluded.checked_at
            """,
            (
                nvr_id,
                camera_id,
                window_start,
                window_end,
                completeness,
                missing_seconds,
                now,
            ),
        )

    def upsert_snapshot(
        self,
        nvr_id: int,
        camera_id: str,
        *,
        jpeg_bytes: bytes,
        width: int,
        height: int,
    ) -> None:
        """2026-07-29（Wall 縮圖重構）：寫/更新單台 cam 的最新縮圖。

        同一 (nvr_id, camera_id) 第二次呼叫會覆蓋（最新快照勝出）。
        jpeg_bytes 應為 Pillow 縮圖後的 JPEG（典型 ~5KB，160x120）。
        """
        conn = self._require_active()
        now = _now_utc_iso()
        conn.execute(
            """
            INSERT INTO camera_snapshots
                (nvr_id, camera_id, jpeg_bytes, width, height, captured_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(nvr_id, camera_id) DO UPDATE SET
                jpeg_bytes = excluded.jpeg_bytes,
                width = excluded.width,
                height = excluded.height,
                captured_at = excluded.captured_at
            """,
            (nvr_id, camera_id, jpeg_bytes, width, height, now),
        )

    def insert_events(
        self,
        scan_run_id: int,
        nvr_id: int,
        events: list[dict],
    ) -> None:
        """
        寫入本次掃描偵測到的異常事件。

        **去重邏輯**（Phase 2.6+）：若 (nvr_id, device_id, event_topic)
        已存在 OPEN 事件（resolved_at IS NULL）→ 不 insert，改 UPDATE
        detected_at / occurred_at 為新值。這樣「同問題未解決」不會越積越多。

        Args:
            scan_run_id: 來自 begin_scan_run()。
            nvr_id: 來自 upsert_nvr()。
            events: 異常事件 list（每筆需含 deviceId / eventTopics 或 eventTopic）。

        Raises:
            RuntimeError: 無 active scan_run 或 scan_run_id 不符。
        """
        conn = self._require_active()
        if scan_run_id != self._current_scan_run_id:
            raise RuntimeError(
                f"scan_run_id 不符：active={self._current_scan_run_id}, "
                f"傳入={scan_run_id}"
            )
        now = _now_utc_iso()
        for ev in events:
            topics = ev.get("eventTopics") or []
            if isinstance(topics, str):
                topics = [topics]
            primary_topic = str(
                ev.get("eventTopic") or (topics[0] if topics else "UNKNOWN")
            )
            device_id = str(ev.get("deviceId", ""))
            new_occurred = str(ev.get("occurred_at") or now)

            # === 去重：同 (nvr_id, device_id, event_topic) 還 OPEN → UPDATE 不 INSERT ===
            existing = conn.execute(
                """
                SELECT id FROM events
                WHERE nvr_id = ? AND device_id = ? AND event_topic = ?
                  AND resolved_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (nvr_id, device_id, primary_topic),
            ).fetchone()
            if existing:
                # 已 OPEN → 更新時間（最近一次發現）
                conn.execute(
                    """
                    UPDATE events
                    SET detected_at = ?, occurred_at = ?, scan_run_id = ?
                    WHERE id = ?
                    """,
                    (now, new_occurred, scan_run_id, existing["id"]),
                )
            else:
                # 全新事件 → INSERT
                conn.execute(
                    """
                    INSERT INTO events (
                        scan_run_id, nvr_id, event_id, device_id,
                        event_topic, event_topics_json, occurred_at,
                        detected_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scan_run_id,
                        nvr_id,
                        str(
                            ev.get("eventId")
                            or ev.get("event_id")
                            or f"auto-{now}-{ev.get('deviceId', '?')}"
                        ),
                        device_id,
                        primary_topic,
                        json.dumps(topics, ensure_ascii=False),
                        new_occurred,
                        now,
                        json.dumps(ev, ensure_ascii=False, default=str),
                    ),
                )

    def insert_image_health_check(
        self,
        nvr_server_id: int,
        camera_id: str,
        *,
        checked_at_utc: str,
        metrics_json: str,
        flags_json: str,
        triggered_event_ids: list[int] | None = None,
    ) -> int:
        """Phase 2.8（Arisan 影像健康巡檢）：寫 image_health_checks + 更新 cameras.last_health_check_id。"""
        conn = self._require_active()
        cur = conn.execute(
            """
            INSERT INTO image_health_checks
                (camera_id, nvr_server_id, checked_at_utc,
                 metrics_json, flags_json, triggered_event_ids)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                camera_id,
                nvr_server_id,
                checked_at_utc,
                metrics_json,
                flags_json,
                json.dumps(triggered_event_ids or []),
            ),
        )
        new_id = cur.lastrowid
        # 反查 cameras.id → 更新 last_health_check_id
        cam_row = conn.execute(
            "SELECT id FROM cameras WHERE nvr_server_id=? AND device_id=?",
            (nvr_server_id, camera_id),
        ).fetchone()
        if cam_row:
            conn.execute(
                "UPDATE cameras SET last_health_check_id=? WHERE id=?",
                (new_id, cam_row["id"]),
            )
        return new_id

    def mark_resolved(
        self,
        scan_run_id: int,
        nvr_id: int,
        *,
        resolved_at: str | None = None,
    ) -> int:
        """
        將此 NVR「這次 scan 沒再出現」的先前 OPEN 事件標記為已解決（Phase 1）。

        邏輯：
            - 對該 NVR 所有 `resolved_at IS NULL` 的 prior events
            - 若該 device 也在這次 scan_run 內有 event → 視為仍異常（不動）
            - 若該 device 這次 scan_run 沒 event → 標記為 resolved_at = NOW()

        範例：scan_1 cam-d1 異常 → scan_2 cam-d1 正常
              → scan_2 insert_events 完呼叫本方法 → scan_1 那筆 resolved_at 被填入

        Args:
            scan_run_id: 本次 scan_run ID（用於排除仍異常的 device）。
            nvr_id: 本 NVR 的內部 ID。
            resolved_at: 自訂時間（UTC ISO 8601 字串）；None 表示用 NOW()。

        Returns:
            被更新的 row 數（int）。

        Raises:
            RuntimeError: 無 active scan_run 或 scan_run_id 不符。
        """
        conn = self._require_active()
        if scan_run_id != self._current_scan_run_id:
            raise RuntimeError(
                f"scan_run_id 不符：active={self._current_scan_run_id}, "
                f"傳入={scan_run_id}"
            )
        when = resolved_at or _now_utc_iso()

        # Week 3 Issue #008：先 SELECT COUNT 拿到實際匹配數。
        # 原因：events 是 view，UPDATE 透過 INSTEAD OF trigger 執行，
        # 但 cur.rowcount 在 INSTEAD OF trigger 下永遠回傳 0（SQLite 已知限制）。
        # 用 SELECT COUNT 預先算好（view 可正常查詢），UPDATE 仍透過 trigger 改資料。
        count_row = conn.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE resolved_at IS NULL
              AND nvr_id = ?
              AND device_id NOT IN (
                SELECT device_id FROM events
                WHERE scan_run_id = ? AND nvr_id = ?
              )
            """,
            (nvr_id, scan_run_id, nvr_id),
        ).fetchone()
        count = count_row[0] if count_row else 0

        conn.execute(
            """
            UPDATE events
            SET resolved_at = ?
            WHERE resolved_at IS NULL
              AND nvr_id = ?
              AND device_id NOT IN (
                SELECT device_id FROM events
                WHERE scan_run_id = ? AND nvr_id = ?
              )
            """,
            (when, nvr_id, scan_run_id, nvr_id),
        )
        return count

    def finish_scan_run(
        self,
        scan_run_id: int,
        *,
        finished_at: str,
        status: str,
        stats: dict,
    ) -> None:
        """
        結束 scan_run，更新統計欄位並 commit transaction。

        Args:
            scan_run_id: 來自 begin_scan_run()。
            finished_at: ISO 8601 UTC 字串。
            status: 'success' / 'partial' / 'failed'。
            stats: 支援以下 keys（皆選填，缺項預設 0）：
                - total_cameras: int
                - abnormal_cameras: int
                - total_nvrs: int（批次層級 NVR 總數，v1 batch_scan 用）
                - ok_nvrs: int
                - failed_nvrs: int

        Raises:
            RuntimeError: 無 active scan_run 或 scan_run_id 不符。
        """
        conn = self._require_active()
        if scan_run_id != self._current_scan_run_id:
            raise RuntimeError(
                f"scan_run_id 不符：active={self._current_scan_run_id}, "
                f"傳入={scan_run_id}"
            )
        conn.execute(
            """
            UPDATE scan_runs SET
                finished_at = ?, status = ?,
                total_cameras = ?, abnormal_cameras = ?,
                total_nvrs = ?, ok_nvrs = ?, failed_nvrs = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                int(stats.get("total_cameras", 0)),
                int(stats.get("abnormal_cameras", 0)),
                int(stats.get("total_nvrs", 0)),
                int(stats.get("ok_nvrs", 0)),
                int(stats.get("failed_nvrs", 0)),
                scan_run_id,
            ),
        )
        conn.commit()
        # 連線保持開啟（:memory: 模式下 close 會丟失整個 DB）
        # 只清掉 active scan_run 標記，下次 begin_scan_run 可重用
        self._current_scan_run_id = None

    # --- Phase 2.7+：個別 NVR 連線失敗記錄 ---

    def log_nvr_failure(
        self,
        scan_run_id: int,
        nvr_id: str,
        nvr_name: str,
        error_type: str,
        error_message: str,
        *,
        nvr_internal_id: int | None = None,
        failed_at: str | None = None,
    ) -> int:
        """
        記錄單台 NVR 的連線失敗（給 dashboard / run_detail 顯示個別錯誤用）。

        Args:
            scan_run_id: 來自 begin_scan_run() 的 run id。
            nvr_id: NVR 設定檔的 id 字串（例 "NVR-A"）。
            nvr_name: 顯示名稱（snapshot，nvr_config 改了不影響歷史）。
            error_type: 例 "ConnectionError" / "Timeout" / "AuthError"。
            error_message: 完整錯誤訊息。
            nvr_internal_id: nvr_servers.id（nullable：upsert 失敗時無 internal id）。
            failed_at: ISO 8601 UTC（預設 = 現在）。

        Returns:
            新插入 row 的 id。

        Note:
            呼叫時機：在 batch_scan 的 except handler 內、commit transaction 之前。
            不會主動 commit；由 finish_scan_run 的 commit 一起持久化。
        """
        conn = self._get_conn()
        # 注意：_require_active 會驗 active scan_run，
        # 但這裡只讀 scan_run_id 對不對，不該限制 call timing
        if (
            self._current_scan_run_id is not None
            and scan_run_id != self._current_scan_run_id
        ):
            raise RuntimeError(
                f"scan_run_id 不符：active={self._current_scan_run_id}, "
                f"傳入={scan_run_id}"
            )
        when = failed_at or _now_utc_iso()
        cur = conn.execute(
            """
            INSERT INTO nvr_failure_log (
                scan_run_id, nvr_id, nvr_name, nvr_internal_id,
                error_type, error_message, failed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_run_id,
                nvr_id,
                nvr_name,
                nvr_internal_id,
                error_type,
                error_message,
                when,
            ),
        )
        return int(cur.lastrowid)

    # --- 查詢輔助（v1 報表用） ---
    def get_scan_runs(self, limit: int = 10) -> list[dict]:
        """查詢最近 N 次 scan_run（v2 Web UI 用，v1 也可用於除錯）。"""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status,
                   total_cameras, abnormal_cameras, error_message
            FROM scan_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_events_for_run(self, scan_run_id: int) -> list[dict]:
        """查詢某次掃描的所有事件。"""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT e.event_topic, e.device_id, c.camera_name,
                   e.event_topics_json, e.occurred_at
            FROM events e
            LEFT JOIN cameras c
                ON e.nvr_id = c.nvr_id AND e.device_id = c.device_id
            WHERE e.scan_run_id = ?
            ORDER BY e.id
            """,
            (scan_run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 連線釋放 ──────────────────────────────────────────────
    def close(self) -> None:
        """關閉 SQLite 連線並釋放 file handle。

        必須在 SqliteWriter 不再使用時呼叫，否則在 Windows 上 DB file handle
        會殘留到 process 結束（特別是 daemon thread 內的 short-lived writer）。
        Idempotent：多次呼叫安全。
        """
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "SqliteWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        # 最後保險：gc 時若還沒 close，嘗試關閉
        try:
            self.close()
        except Exception:
            pass


# ── 跨 process 互斥鎖 ────────────────────────────────────────
def acquire_scan_lock(db_path: str, *, timeout: float = 0.0) -> bool:
    """搶「正在掃描」狀態的鎖（跨 process 安全）。

    透過 SQLite BEGIN IMMEDIATE 取得 RESERVED lock。
    若 DB 內已有 status='running' 的 scan_run 直接 return False。

    Args:
        db_path: SQLite 檔案路徑
        timeout: 等待時間（秒），0 = 不等待

    Returns:
        True = 取得鎖，可繼續；False = 已有他人執行
    """
    import sqlite3
    import time

    deadline = time.monotonic() + timeout
    while True:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            # 檢查既有 running scan
            row = conn.execute(
                "SELECT id FROM scan_runs WHERE status='running' LIMIT 1"
            ).fetchone()
            if row:
                return False
            # 嘗試拿 RESERVED lock（確保寫入不衝突）
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")  # 馬上釋放，不寫任何東西
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and time.monotonic() < deadline:
                conn.close()
                time.sleep(0.5)
                continue
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass
