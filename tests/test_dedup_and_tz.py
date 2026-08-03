"""
tests/test_dedup_and_tz.py
==========================
Phase 2.6+：
    - insert_events 去重（同 device+topic OPEN → 不 insert，改 UPDATE）
    - 時區：UTC → Asia/Taipei 顯示（_to_taipei_str + Jinja filter）
"""
from __future__ import annotations

import gc
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import _to_taipei_str, create_app


# === Fixture ===

@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        p = f.name
    yield p
    import gc as _gc
    _gc.collect()
    Path(p).unlink(missing_ok=True)


# === 1. 時區 helper ===

def test_to_taipei_str_utc_z():
    """UTC 'Z' 格式 → +8 小時。"""
    assert _to_taipei_str("2026-07-03T05:39:02Z") == "2026-07-03 13:39:02"


def test_tzdata_can_load_asia_taipei():
    """守門測試：確保 tzdata 套件齊全（沒裝會 ZoneInfoNotFoundError）。

    Windows 沒有 system tz database，必須靠 tzdata 套件。
    requirements.txt 已強制 tzdata>=2024.1。
    """
    tz = ZoneInfo("Asia/Taipei")
    from datetime import datetime
    dt = datetime(2026, 7, 3, 13, 39, 2, tzinfo=tz)
    assert dt.utcoffset().total_seconds() == 8 * 3600


def test_to_taipei_str_iso_with_offset():
    """含 +00:00 offset 也行。"""
    assert _to_taipei_str("2026-07-03T05:39:02+00:00") == "2026-07-03 13:39:02"


def test_to_taipei_str_naive():
    """naive datetime（沒時區）視為 UTC。"""
    assert _to_taipei_str("2026-07-03T05:39:02") == "2026-07-03 13:39:02"


def test_to_taipei_str_empty():
    """空字串 / None 維持原樣。"""
    assert _to_taipei_str("") == ""
    assert _to_taipei_str(None) == ""


def test_to_taipei_str_invalid():
    """壞字串回傳原文（不拋 exception）。"""
    assert _to_taipei_str("not a date") == "not a date"


def test_to_taipei_str_cross_day():
    """跨日：UTC 17:00 → 台北 01:00（隔天）。"""
    assert _to_taipei_str("2026-07-03T17:00:00Z") == "2026-07-04 01:00:00"


# === 2. Jinja filter 整合 ===

def test_jinja_taipei_filter(db_path):
    """Jinja filter 'taipei' 正確掛在 app。"""
    SqliteWriter(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        # 沒有任何 route 處理 taipei filter，但 Jinja 環境要能直接呼叫
        env = app.jinja_env
        assert "taipei" in env.filters
        result = env.filters["taipei"]("2026-07-03T05:39:02Z")
        assert result == "2026-07-03 13:39:02"
    del app
    gc.collect()


# === 3. insert_events 去重 ===

def test_insert_events_dedup_same_topic_open(db_path):
    """同 (nvr_id, device_id, event_topic) 還 OPEN → 不 insert，改 UPDATE。"""
    w = SqliteWriter(db_path)
    nvr = w.upsert_nvr({
        "id": "NVR-X", "name": "X", "host": "1.1.1.1",
        "port": 8443, "username": "u", "password": "p",
    })
    run1 = w.begin_scan_run("2026-07-03T05:00:00Z")
    w.upsert_cameras(nvr, {1: "Cam-1"})
    # 第一次 scan：插 1 筆
    w.insert_events(run1, nvr, [{
        "deviceId": 1, "eventTopic": "STATE_DISCONNECTED",
        "eventTopics": ["STATE_DISCONNECTED"],
        "occurred_at": "2026-07-03T05:01:00Z",
    }])
    w.finish_scan_run(
        run1, finished_at="2026-07-03T05:02:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1},
    )

    # 第二次 scan：同 device 同 topic 還 OPEN → 不 insert，改 UPDATE
    run2 = w.begin_scan_run("2026-07-03T06:00:00Z")
    w.insert_events(run2, nvr, [{
        "deviceId": 1, "eventTopic": "STATE_DISCONNECTED",
        "eventTopics": ["STATE_DISCONNECTED"],
        "occurred_at": "2026-07-03T06:01:00Z",
    }])
    w.finish_scan_run(
        run2, finished_at="2026-07-03T06:02:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1},
    )
    del w

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, scan_run_id, detected_at FROM events ORDER BY id"
    ).fetchall()
    conn.close()
    # 應該只有 1 筆（不重複 insert）
    assert len(rows) == 1
    # 第二次 scan 的時間應該被記錄（detected_at = scan 當下時間）
    # 不嚴格比對時間，只要 run_id 是第二次的就 OK
    assert rows[0][1] == run2  # 第二次 scan 的 id


def test_insert_events_new_topic_inserts(db_path):
    """同 device、不同 topic → 還是 insert。"""
    w = SqliteWriter(db_path)
    nvr = w.upsert_nvr({
        "id": "NVR-X", "name": "X", "host": "1.1.1.1",
        "port": 8443, "username": "u", "password": "p",
    })
    run1 = w.begin_scan_run("2026-07-03T05:00:00Z")
    w.upsert_cameras(nvr, {1: "Cam-1"})
    w.insert_events(run1, nvr, [{
        "deviceId": 1, "eventTopic": "STATE_DISCONNECTED",
        "occurred_at": "2026-07-03T05:01:00Z",
    }])
    w.finish_scan_run(
        run1, finished_at="2026-07-03T05:02:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1},
    )

    # 第二次 scan：不同 topic → 仍 insert 新 row
    run2 = w.begin_scan_run("2026-07-03T06:00:00Z")
    w.insert_events(run2, nvr, [{
        "deviceId": 1, "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-07-03T06:01:00Z",
    }])
    w.finish_scan_run(
        run2, finished_at="2026-07-03T06:02:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1},
    )
    del w

    import sqlite3
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    topics = [r[0] for r in conn.execute(
        "SELECT DISTINCT event_topic FROM events"
    ).fetchall()]
    conn.close()
    assert n == 2
    assert "STATE_DISCONNECTED" in topics
    assert "STATE_LONG_FAILED" in topics


def test_insert_events_resolved_allows_reinsert(db_path):
    """同 device 同 topic 已被 resolved → 下次又異常時 insert 新 row。"""
    w = SqliteWriter(db_path)
    nvr = w.upsert_nvr({
        "id": "NVR-X", "name": "X", "host": "1.1.1.1",
        "port": 8443, "username": "u", "password": "p",
    })
    run1 = w.begin_scan_run("2026-07-03T05:00:00Z")
    w.upsert_cameras(nvr, {1: "Cam-1"})
    w.insert_events(run1, nvr, [{
        "deviceId": 1, "eventTopic": "STATE_DISCONNECTED",
        "occurred_at": "2026-07-03T05:01:00Z",
    }])
    w.finish_scan_run(
        run1, finished_at="2026-07-03T05:02:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1},
    )

    # 標記為 resolved
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE events SET resolved_at = '2026-07-03T05:30:00Z' WHERE id = 1"
    )
    conn.commit()
    conn.close()

    # 第三次 scan：device 又異常 → 應 insert 新 row（因為舊的已 resolved）
    run3 = w.begin_scan_run("2026-07-03T07:00:00Z")
    w.insert_events(run3, nvr, [{
        "deviceId": 1, "eventTopic": "STATE_DISCONNECTED",
        "occurred_at": "2026-07-03T07:01:00Z",
    }])
    w.finish_scan_run(
        run3, finished_at="2026-07-03T07:02:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1},
    )
    del w

    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    open_n = conn.execute(
        "SELECT COUNT(*) FROM events WHERE resolved_at IS NULL"
    ).fetchone()[0]
    conn.close()
    assert n == 2
    assert open_n == 1  # 舊的 resolved，新的 OPEN