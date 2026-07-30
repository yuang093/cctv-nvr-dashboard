"""
tests/test_nvr_host_dedup.py
============================
2026-07-29 修 bug：bulk_upsert_nvrs 沒偵測 (host, port) 重複，
導致 nvr_config.json 改了 id 後，DB 會建第二筆同 host NVR → /wall 顯示重複 cam。

修法：bulk 前先查同 (host, port) 是否已有不同 nvr_id 的 row，
若已有 → 把既有 row 的 nvr_id 改為新 alias（merge），
確保「一台 NVR 對應 DB 一筆 row」的不變量。
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import bulk_upsert_nvrs


# === 1. 同 host 不同 nvr_id → 觸發 migration（建 NEW row，migrate OLD 資料，DELETE OLD）===
def test_bulk_upsert_dedup_same_host_different_nvr_id():
    """DB 已有 nvr_id='ACC8-P4' host=192.168.133.141，
    再 bulk_upsert nvr_id='WIN-OPA34I3TCL5' 同 host →
    建 NEW row、migrate 旗下資料（如有）、DELETE OLD row。
    最終只剩 1 筆 nvr_servers，內部 id 是新建的（不是舊的）。
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "ACC8-P4", "name": "ACC8", "host": "192.168.133.141",
        "port": 8443, "username": "u", "password": "p",
    })
    del w
    gc.collect()

    result = bulk_upsert_nvrs(db_path, [{
        "nvr_id": "WIN-OPA34I3TCL5", "name": "Windows主機",
        "host": "192.168.133.141", "port": 8443,
        "username": "u", "password": "p",
    }])
    # NEW row 是新 INSERT（不是 merge）
    assert result["inserted"] == 1
    assert result["updated"] == 0

    # DB 檢查：只有 1 筆 NVR，nvr_id 是新的，internal_id 是新建的
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, nvr_id, host FROM nvr_servers"
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"應只有 1 筆 NVR，實際 {len(rows)} 筆"
    internal_id, nvr_id, host = rows[0]
    assert nvr_id == "WIN-OPA34I3TCL5"
    assert host == "192.168.133.141"
    assert internal_id != nvra, "NEW row 應是新 internal_id（舊的已被 DELETE）"

    Path(db_path).unlink(missing_ok=True)


# === 2. 不同 host 都 INSERT ===
def test_bulk_upsert_different_host_inserts():
    """兩個不同 host → 兩筆 row（沒衝突）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    del w
    gc.collect()

    result = bulk_upsert_nvrs(db_path, [{
        "nvr_id": "NVR-B", "name": "B", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    }])
    assert result["inserted"] == 1
    assert result["updated"] == 0

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT nvr_id, host FROM nvr_servers").fetchall()
    conn.close()
    assert len(rows) == 2

    Path(db_path).unlink(missing_ok=True)


# === 3. 同 host 不同 port 視為不同 NVR ===
def test_bulk_upsert_same_host_different_port_inserts():
    """同 host 但 port 不同（罕見但合理）→ 兩筆 row。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    del w
    gc.collect()

    result = bulk_upsert_nvrs(db_path, [{
        "nvr_id": "NVR-B", "name": "B", "host": "10.0.0.1",
        "port": 8444, "username": "u", "password": "p",
    }])
    assert result["inserted"] == 1
    assert result["updated"] == 0

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT nvr_id, host, port FROM nvr_servers").fetchall()
    conn.close()
    assert len(rows) == 2
    ports = sorted(r[2] for r in rows)
    assert ports == [8443, 8444]

    Path(db_path).unlink(missing_ok=True)


# === 4. 同 nvr_id 已存在 → 走原本 update 流程（不影響既有行為）===
def test_bulk_upsert_existing_nvr_id_updates_normally():
    """nvr_id 已存在 → 走原本的 update 流程（merge 不觸發）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "NVR-A", "name": "舊名", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    del w
    gc.collect()

    result = bulk_upsert_nvrs(db_path, [{
        "nvr_id": "NVR-A", "name": "新名", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    }])
    assert result["inserted"] == 0
    assert result["updated"] == 1

    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT name FROM nvr_servers WHERE nvr_id='NVR-A'"
    ).fetchone()
    conn.close()
    assert row[0] == "新名"

    Path(db_path).unlink(missing_ok=True)


# === 5. 空 DB → 直接 INSERT（沒有 merge 觸發）===
def test_bulk_upsert_empty_db_inserts():
    """DB 全空 → 直接 INSERT，不走 merge 分支。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # 先 init schema（空 DB 沒 nvr_servers table）
    SqliteWriter(db_path)

    result = bulk_upsert_nvrs(db_path, [{
        "nvr_id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    }])
    assert result["inserted"] == 1
    assert result["updated"] == 0

    Path(db_path).unlink(missing_ok=True)


# === 6. dedup 時自動 migrate OLD NVR 的全部資料（2026-07-30 Task A）===
def test_bulk_upsert_dedup_migrates_all_related_data():
    """host:port 撞到 OLD NVR（不同 nvr_id）→ 自動把 OLD 的
    cameras / events / image_health_checks / recording_status / camera_snapshots
    全部 migrate 到 NEW，然後 DELETE OLD row。

    場景：使用者編輯 nvr_config.json 改 nvr_id，但 DB 已有舊 nvr_id 的 row + 其下資料。
    不 migrate 就會留下 orphan → /abnormal 顯示幽靈相機、dashboard 顯示舊資料。
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    old_nvr = w.upsert_nvr({
        "id": "OLD-NAME", "name": "old", "host": "192.168.133.141",
        "port": 8443, "username": "u", "password": "p",
    })
    # 給 OLD NVR 灌一輪 scan + 各類資料
    run_id = w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(old_nvr, {
        "cam-1": {"name": "cam-1", "connection_state": "CONNECTED"},
    })
    w.insert_events(run_id, old_nvr, [{
        "deviceId": "cam-1",
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "occurred_at": "2026-07-30T00:00:00Z",
    }])
    w.finish_scan_run(run_id, finished_at="2026-07-30T00:01:00Z",
                      status="success", stats={"total_cameras": 1})
    w.begin_scan_run("2026-07-30T00:02:00Z")
    w.upsert_recording_status(old_nvr, "cam-1",
        window_start="2026-07-29T00:00:00Z",
        window_end="2026-07-30T00:00:00Z",
        completeness=0.95, missing_seconds=0.5*3600)
    # image_health_checks 也要有資料（這次 bug 的主因之一）
    w._get_conn().execute(
        """
        INSERT INTO image_health_checks
            (camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json)
        VALUES ('cam-1', ?, '2026-07-30T00:01:00Z', '{}', '["frozen"]')
        """,
        (old_nvr,),
    )
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()

    # 觸發 dedup：同 host 但 nvr_id 改成新名稱
    bulk_upsert_nvrs(db_path, [{
        "nvr_id": "NEW-NAME", "name": "new",
        "host": "192.168.133.141", "port": 8443,
        "username": "u", "password": "p",
    }])

    # 驗證：DB 內只有 1 筆 NVR（NEW-NAME）
    import sqlite3
    conn = sqlite3.connect(db_path)
    nvrs = conn.execute(
        "SELECT id, nvr_id FROM nvr_servers"
    ).fetchall()
    conn.close()
    assert len(nvrs) == 1, f"OLD 應被 DELETE，剩 1 筆，實際 {len(nvrs)} 筆"
    new_internal_id = nvrs[0][0]
    assert nvrs[0][1] == "NEW-NAME"

    # 驗證：cameras / events / recording_status / image_health_checks 都指向 NEW
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE nvr_id=?", (new_internal_id,)
    ).fetchone()[0] == 1, "cam-1 應被 migrate 到 NEW"
    assert conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE nvr_id=?", (old_nvr,)
    ).fetchone()[0] == 0, "OLD 不應還有 cam"
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE nvr_id=?", (new_internal_id,)
    ).fetchone()[0] == 1, "event 應被 migrate"
    assert conn.execute(
        "SELECT COUNT(*) FROM image_health_checks WHERE nvr_server_id=?", (new_internal_id,)
    ).fetchone()[0] == 1, "image_health 應被 migrate"
    assert conn.execute(
        "SELECT COUNT(*) FROM recording_status WHERE nvr_id=?", (new_internal_id,)
    ).fetchone()[0] == 1, "recording_status 應被 migrate"
    conn.close()

    Path(db_path).unlink(missing_ok=True)


# === 7. dedup 不影響其他 host 的資料 ===
def test_bulk_upsert_dedup_only_affects_colliding_host():
    """host:port 不衝突的 NVR 應保持原狀，不被 migrate。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    old_unchanged = w.upsert_nvr({
        "id": "UNRELATED", "name": "u", "host": "10.0.0.99",
        "port": 8443, "username": "u", "password": "p",
    })
    w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(old_unchanged, {
        "c1": {"name": "c1", "connection_state": "CONNECTED"},
    })
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()

    # bulk_upsert 不同 host 的 NVR（不應觸發 unrelated 的 migrate）
    bulk_upsert_nvrs(db_path, [{
        "nvr_id": "OTHER", "name": "other", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    }])

    import sqlite3
    conn = sqlite3.connect(db_path)
    # UNRELATED 的 cam 還在 UNRELATED 下
    assert conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE nvr_id=?", (old_unchanged,)
    ).fetchone()[0] == 1
    # 2 筆 NVR 都在
    assert conn.execute(
        "SELECT COUNT(*) FROM nvr_servers"
    ).fetchone()[0] == 2
    conn.close()

    Path(db_path).unlink(missing_ok=True)


# === 8. 同 nvr_id 已存在 + 同 host（純改欄位）→ 不算 collision ===
def test_bulk_upsert_existing_nvr_id_no_collision_migration():
    """nvr_id 已存在、同 host → 不觸發 migration（避免把同 row 資料搬到同 row）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    existing = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(existing, {"c1": {"name": "c1", "connection_state": "CONNECTED"}})
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()

    bulk_upsert_nvrs(db_path, [{
        "nvr_id": "NVR-A", "name": "A 新名", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    }])

    import sqlite3
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE nvr_id=?", (existing,)
    ).fetchone()[0] == 1, "cam 應還掛在原本的 internal_id"
    conn.close()

    Path(db_path).unlink(missing_ok=True)
