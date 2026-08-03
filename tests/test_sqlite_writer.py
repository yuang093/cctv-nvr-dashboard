"""
tests/test_sqlite_writer.py
============================
SqliteWriter 單元測試。

覆蓋：
    - 基本 lifecycle
    - 查詢 + JOIN 解析 camera_name
    - 連續兩次 scan_run 共用連線
    - 檔案 DB 持久化（跨 instance 讀取）
    - rollback（未 finish → close 後資料消失）
    - 錯誤處理（無 begin 就 upsert → RuntimeError）
    - 新增：batch stats 欄位寫入
    - 新增：向下相容（不帶新欄位 → 預設 0）
"""
from __future__ import annotations

import gc
import json
from datetime import datetime

import pytest

from db.sqlite_writer import SqliteWriter


def _seed_full_scan(w: "SqliteWriter", nvr_id: str = "n1") -> int:
    """helper：跑一次完整 lifecycle（upsert_nvr → begin → cameras → events → finish）。"""
    nvr_int = w.upsert_nvr({
        "id": nvr_id, "name": nvr_id, "host": "1.1.1.1",
        "username": "u", "password": "p",
    })
    run_id = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.upsert_cameras(nvr_int, {"d1": "cam1", "d2": "cam2"})
    w.insert_events(run_id, nvr_int, [
        {
            "eventId": "e1", "deviceId": "d1",
            "eventTopics": ["X"], "eventTopic": "X",
            "occurred_at": "2026-06-23T00:00:00Z",
        },
    ])
    return nvr_int, run_id


# === 1. 基本 lifecycle ===
def test_basic_lifecycle(memory_db):
    w = memory_db
    nvr_int, run_id = _seed_full_scan(w)
    w.finish_scan_run(
        run_id, finished_at="2026-06-23T00:01:00Z",
        status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 1},
    )
    rows = w.get_scan_runs(5)
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["total_cameras"] == 2
    assert rows[0]["abnormal_cameras"] == 1


# === 2. 查詢驗證 + JOIN cameras ===
def test_query_with_join(memory_db):
    w = memory_db
    nvr_int, run_id = _seed_full_scan(w, "n2")
    w.finish_scan_run(
        run_id, finished_at="2026-06-23T00:01:00Z",
        status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 1},
    )
    events = w.get_events_for_run(run_id)
    assert len(events) == 1
    assert events[0]["camera_name"] == "cam1"
    assert events[0]["event_topic"] == "X"


# === 3. 連續兩次 scan_run 共用連線 ===
def test_consecutive_scan_runs(memory_db):
    w = memory_db
    # 第一次
    nvr_int, r1 = _seed_full_scan(w, "n3")
    w.finish_scan_run(
        r1, finished_at="2026-06-23T00:01:00Z",
        status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 1},
    )
    # 第二次
    r2 = w.begin_scan_run("2026-06-23T00:02:00Z")
    w.upsert_cameras(nvr_int, {"d3": "cam3"})
    w.insert_events(r2, nvr_int, [{
        "eventId": "e2", "deviceId": "d3",
        "eventTopics": ["Y"], "eventTopic": "Y",
        "occurred_at": "2026-06-23T00:02:00Z",
    }])
    w.finish_scan_run(
        r2, finished_at="2026-06-23T00:03:00Z",
        status="success",
        stats={"total_cameras": 3, "abnormal_cameras": 1},
    )
    runs = w.get_scan_runs(10)
    assert len(runs) == 2


# === 4. 檔案 DB 持久化 ===
def test_file_db_persistence(tmp_db, tmp_path):
    w = tmp_db
    nvr_int, run_id = _seed_full_scan(w, "n4")
    w.finish_scan_run(
        run_id, finished_at="2026-06-23T00:01:00Z",
        status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 1},
    )
    # 刪除連線（gc 釋放檔案 lock）
    del w
    gc.collect()
    # 重新連線讀
    from db.sqlite_writer import SqliteWriter
    w2 = SqliteWriter(str(tmp_path / "test.db"))
    runs = w2.get_scan_runs(5)
    assert len(runs) == 1
    assert runs[0]["status"] == "success"


# === 5. rollback（未 finish 就 close → 資料消失）===
def test_rollback_on_close(tmp_path):
    db_path = str(tmp_path / "rollback.db")
    w = SqliteWriter(db_path)
    w.upsert_nvr({"id": "n5", "name": "n5", "host": "1.1.1.1",
                  "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.upsert_cameras(1, {"d1": "cam1"})
    w.insert_events(rid, 1, [{
        "eventId": "e1", "deviceId": "d1",
        "eventTopics": ["X"], "eventTopic": "X",
        "occurred_at": "2026-06-23T00:00:00Z",
    }])
    # 不 finish，直接 close → transaction 應 rollback
    w._conn.close()
    del w
    gc.collect()
    w2 = SqliteWriter(db_path)
    runs = w2.get_scan_runs(5)
    assert len(runs) == 0  # 沒 commit → 沒資料
    evs = w2._conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    assert evs == 0


# === 6. 錯誤處理：無 begin 就 upsert_cameras ===
def test_error_without_begin(memory_db):
    w = memory_db
    w.upsert_nvr({"id": "n6", "name": "n6", "host": "1.1.1.1",
                  "username": "u", "password": "p"})
    with pytest.raises(RuntimeError, match="begin_scan_run"):
        w.upsert_cameras(1, {"d1": "cam1"})


# === 7. 新增：batch stats 欄位寫入 ===
def test_batch_stats_fields(memory_db):
    """finish_scan_run 接受 total_nvrs/ok_nvrs/failed_nvrs 寫入 DB。"""
    w = memory_db
    w.upsert_nvr({"id": "n7", "name": "n7", "host": "1.1.1.1",
                  "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.finish_scan_run(
        rid, finished_at="2026-06-23T00:01:00Z",
        status="partial",
        stats={
            "total_cameras": 10,
            "abnormal_cameras": 3,
            "total_nvrs": 5,
            "ok_nvrs": 3,
            "failed_nvrs": 2,
        },
    )
    row = w._conn.execute(
        "SELECT total_nvrs, ok_nvrs, failed_nvrs, total_cameras, "
        "abnormal_cameras, status FROM scan_runs WHERE id=?",
        (rid,),
    ).fetchone()
    assert row["total_nvrs"] == 5
    assert row["ok_nvrs"] == 3
    assert row["failed_nvrs"] == 2
    assert row["total_cameras"] == 10
    assert row["abnormal_cameras"] == 3
    assert row["status"] == "partial"


# === 8. 向下相容：不帶新欄位 → 預設 0 ===
def test_backward_compat_finish_scan_run(memory_db):
    """舊呼叫（只帶 total_cameras/abnormal_cameras）仍正常，新欄位預設 0。"""
    w = memory_db
    w.upsert_nvr({"id": "n8", "name": "n8", "host": "1.1.1.1",
                  "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.finish_scan_run(
        rid, finished_at="2026-06-23T00:01:00Z",
        status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 0},
        # 故意不帶 total_nvrs/ok_nvrs/failed_nvrs
    )
    row = w._conn.execute(
        "SELECT total_nvrs, ok_nvrs, failed_nvrs FROM scan_runs WHERE id=?",
        (rid,),
    ).fetchone()
    assert row["total_nvrs"] == 0
    assert row["ok_nvrs"] == 0
    assert row["failed_nvrs"] == 0


# === 9. scan_run_id 不符拋錯 ===
def test_scan_run_id_mismatch(memory_db):
    w = memory_db
    w.upsert_nvr({"id": "n9", "name": "n9", "host": "1.1.1.1",
                  "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    with pytest.raises(RuntimeError, match="scan_run_id 不符"):
        w.finish_scan_run(
            rid + 999, finished_at="2026-06-23T00:01:00Z",
            status="success",
            stats={"total_cameras": 0, "abnormal_cameras": 0},
        )


# === 10. 連續 begin 不 finish → RuntimeError ===
def test_double_begin_raises(memory_db):
    w = memory_db
    w.begin_scan_run("2026-06-23T00:00:00Z")
    with pytest.raises(RuntimeError, match="已有進行中的 scan_run"):
        w.begin_scan_run("2026-06-23T00:01:00Z")


# === 11. cameras dict 值相容（純字串 vs 含 name 的 dict）===
def test_cameras_dict_compat(memory_db):
    w = memory_db
    w.upsert_nvr({"id": "n10", "name": "n10", "host": "1.1.1.1",
                  "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    # 純字串值
    w.upsert_cameras(1, {"d1": "純字串名"})
    # dict 值（含 name）
    w.upsert_cameras(1, {
        "d2": {"name": "dict格式名"},
        "d3": {"name": "另一台", "connection_state": "CONNECTED"},
    })
    rows = w._conn.execute(
        "SELECT device_id, camera_name FROM cameras ORDER BY device_id"
    ).fetchall()
    names = {r["device_id"]: r["camera_name"] for r in rows}
    assert names["d1"] == "純字串名"
    assert names["d2"] == "dict格式名"
    assert names["d3"] == "另一台"


# === 12. Phase 1：events 表有 resolved_at 欄位（fresh init）===
def test_resolved_at_column_exists_after_init(memory_db):
    """新 DB 初始化後，events 表應有 resolved_at 欄位且預設 NULL。"""
    w = memory_db
    cols = {
        r["name"]: r["type"]
        for r in w._conn.execute("PRAGMA table_info(events)").fetchall()
    }
    assert "resolved_at" in cols, "events 表應有 resolved_at 欄位"
    # type 應為 TEXT（或 None 表示 nullable）
    assert cols["resolved_at"] in (None, "", "TEXT"), (
        f"resolved_at type 應為 TEXT，got {cols['resolved_at']!r}"
    )


# === 13. Phase 1：對舊 DB（缺 resolved_at）自動 ALTER ===
def test_migrate_old_db_adds_resolved_at(tmp_path):
    """模擬 v1 schema 的 events 表（沒有 resolved_at）→ SqliteWriter 自動補欄位。

    流程：
        1. 直接用 sqlite3 建表（沒有 resolved_at）
        2. 灌一筆假事件
        3. SqliteWriter(db_path) → _init_schema() 應自動 ALTER
        4. resolved_at 應該被加上，舊資料仍完整保留
    """
    import sqlite3

    db_path = str(tmp_path / "old.db")
    # 1. 模擬 v1 schema（沒有 resolved_at）
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nvr_servers (
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
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE (nvr_id, device_id)
        );
        CREATE TABLE scan_runs (
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
        CREATE TABLE events (
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
            raw_json TEXT NOT NULL
        );
    """)
    # 灌一筆資料
    conn.execute(
        "INSERT INTO nvr_servers (nvr_id, name, host, created_at, updated_at) "
        "VALUES ('legacy', 'legacy', '1.1.1.1', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')",
    )
    conn.execute(
        "INSERT INTO scan_runs (started_at, status) VALUES "
        "('2025-01-01T00:00:00Z', 'success')",
    )
    conn.execute(
        "INSERT INTO events (scan_run_id, nvr_id, event_id, device_id, "
        "event_topic, event_topics_json, occurred_at, raw_json) "
        "VALUES (1, 1, 'old-evt', 'd1', 'VIDEO_LOSS', '[\"VIDEO_LOSS\"]', "
        "'2025-01-01T00:00:00Z', '{}')",
    )
    conn.commit()
    conn.close()

    # 2. SqliteWriter 開啟這個舊 DB → 應自動 migrate
    w = SqliteWriter(db_path)
    cols = {
        r["name"] for r in w._conn.execute("PRAGMA table_info(events)").fetchall()
    }
    assert "resolved_at" in cols, "自動 migration 後應有 resolved_at"

    # 3. 舊資料應完整保留
    row = w._conn.execute(
        "SELECT event_id, resolved_at FROM events WHERE event_id='old-evt'",
    ).fetchone()
    assert row["event_id"] == "old-evt"
    assert row["resolved_at"] is None, "舊事件的 resolved_at 應為 NULL"

    # 4. idempotent：再新建一個 SqliteWriter 不應出錯
    w2 = SqliteWriter(db_path)
    cols2 = {
        r["name"] for r in w2._conn.execute("PRAGMA table_info(events)").fetchall()
    }
    assert "resolved_at" in cols2


# === 14. Phase 1：mark_resolved 把上次異常但這次正常的 device 標記 resolved ===
def test_mark_resolved_prior_event_when_current_normal(memory_db):
    """scan_1 d1 異常 → scan_2 d1 沒 event → 標記 scan_1 那筆 resolved。"""
    w = memory_db
    nvr_int = w.upsert_nvr({
        "id": "n11", "name": "n11", "host": "1.1.1.1",
        "username": "u", "password": "p",
    })

    # --- scan_1: d1 異常 ---
    r1 = w.begin_scan_run("2026-06-30T08:00:00Z")
    w.upsert_cameras(nvr_int, {"d1": "cam1", "d2": "cam2"})
    w.insert_events(r1, nvr_int, [{
        "eventId": "ev-prior", "deviceId": "d1",
        "eventTopics": ["VIDEO_LOSS"], "eventTopic": "VIDEO_LOSS",
        "occurred_at": "2026-06-30T08:00:00Z",
    }])
    w.finish_scan_run(
        r1, finished_at="2026-06-30T08:01:00Z", status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 1},
    )

    # --- scan_2: d1 恢復（不 insert 任何 event） ---
    r2 = w.begin_scan_run("2026-06-30T09:00:00Z")
    w.upsert_cameras(nvr_int, {"d1": "cam1", "d2": "cam2"})
    # 故意不 insert_events（d1 / d2 都正常）
    count = w.mark_resolved(r2, nvr_int)
    assert count == 1, "應該把 scan_1 那筆 d1 標記為 resolved"

    row = w._conn.execute(
        "SELECT resolved_at FROM events WHERE event_id='ev-prior'"
    ).fetchone()
    assert row["resolved_at"] is not None, "resolved_at 應已被填入"
    # 應為當下（NOW()）；簡單檢查是 ISO 8601 開頭
    today_prefix = datetime.utcnow().strftime("%Y-%m-%dT")
    assert row["resolved_at"].startswith(today_prefix), (
        f"resolved_at 應為當下 ISO 8601，got {row['resolved_at']!r}"
    )


# === 15. Phase 1：mark_resolved 對仍異常的 device 不動作 ===
def test_mark_resolved_skips_still_abnormal(memory_db):
    """scan_2 d1 還在異常 → 不應被 mark_resolved 動到（仍 OPEN）。"""
    w = memory_db
    nvr_int = w.upsert_nvr({
        "id": "n12", "name": "n12", "host": "1.1.1.1",
        "username": "u", "password": "p",
    })

    # scan_1: d1 異常
    r1 = w.begin_scan_run("2026-06-30T08:00:00Z")
    w.upsert_cameras(nvr_int, {"d1": "cam1"})
    w.insert_events(r1, nvr_int, [{
        "eventId": "ev-still", "deviceId": "d1",
        "eventTopics": ["X"], "eventTopic": "X",
        "occurred_at": "2026-06-30T08:00:00Z",
    }])
    w.finish_scan_run(
        r1, finished_at="2026-06-30T08:01:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1},
    )

    # scan_2: d1 仍異常 → 再 insert 一筆
    r2 = w.begin_scan_run("2026-06-30T09:00:00Z")
    w.upsert_cameras(nvr_int, {"d1": "cam1"})
    w.insert_events(r2, nvr_int, [{
        "eventId": "ev-still-2", "deviceId": "d1",
        "eventTopics": ["X"], "eventTopic": "X",
        "occurred_at": "2026-06-30T09:00:00Z",
    }])
    count = w.mark_resolved(r2, nvr_int)
    assert count == 0, "d1 仍異常，不應被 mark_resolved"

    # 兩筆事件都不應該被 mark
    rows = w._conn.execute(
        "SELECT event_id, resolved_at FROM events ORDER BY id"
    ).fetchall()
    assert all(r["resolved_at"] is None for r in rows)


# === 16. Phase 1：mark_resolved 不跨 NVR 影響 ===
def test_mark_resolved_scoped_to_nvr(memory_db):
    """mark_resolved 只影響指定 nvr_id 的事件，不波及別台 NVR。"""
    w = memory_db
    nvr_a = w.upsert_nvr({
        "id": "nA", "name": "nA", "host": "1.1.1.1",
        "username": "u", "password": "p",
    })
    nvr_b = w.upsert_nvr({
        "id": "nB", "name": "nB", "host": "2.2.2.2",
        "username": "u", "password": "p",
    })

    # scan: 兩台 NVR 各有 d1 異常
    r1 = w.begin_scan_run("2026-06-30T08:00:00Z")
    w.upsert_cameras(nvr_a, {"d1": "camA"})
    w.upsert_cameras(nvr_b, {"d1": "camB"})
    w.insert_events(r1, nvr_a, [{
        "eventId": "ev-a", "deviceId": "d1",
        "eventTopics": ["X"], "occurred_at": "2026-06-30T08:00:00Z",
    }])
    w.insert_events(r1, nvr_b, [{
        "eventId": "ev-b", "deviceId": "d1",
        "eventTopics": ["X"], "occurred_at": "2026-06-30T08:00:00Z",
    }])
    w.finish_scan_run(
        r1, finished_at="2026-06-30T08:01:00Z", status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 2},
    )

    # scan_2: 只有 NVR A 恢復（B 仍異常）
    r2 = w.begin_scan_run("2026-06-30T09:00:00Z")
    w.upsert_cameras(nvr_a, {"d1": "camA"})  # A 正常 → 不 insert_events
    w.upsert_cameras(nvr_b, {"d1": "camB"})
    w.insert_events(r2, nvr_b, [{
        "eventId": "ev-b-2", "deviceId": "d1",
        "eventTopics": ["X"], "occurred_at": "2026-06-30T09:00:00Z",
    }])
    # 只對 NVR A 跑 mark_resolved
    count = w.mark_resolved(r2, nvr_a)
    assert count == 1, "只有 NVR A 的 d1 該被標記"

    a_row = w._conn.execute(
        "SELECT resolved_at FROM events WHERE event_id='ev-a'"
    ).fetchone()
    b_row = w._conn.execute(
        "SELECT resolved_at FROM events WHERE event_id='ev-b'"
    ).fetchone()
    assert a_row["resolved_at"] is not None, "NVR A 的 d1 應被 mark"
    assert b_row["resolved_at"] is None, "NVR B 的 d1 不該被波及"


# === 17. Phase 1：mark_resolved 沒 active scan_run 拋 RuntimeError ===
def test_mark_resolved_no_active_run_raises(memory_db):
    w = memory_db
    w.upsert_nvr({
        "id": "n13", "name": "n13", "host": "1.1.1.1",
        "username": "u", "password": "p",
    })
    with pytest.raises(RuntimeError, match="begin_scan_run"):
        w.mark_resolved(1, 1)
