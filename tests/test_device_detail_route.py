"""
tests/test_device_detail_route.py
=================================
Phase 2.8（Arisan）Phase #5：/devices/<device_id> + /health/cameras/<id> 詳情頁。
"""
from __future__ import annotations

import gc
import json
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def detail_app():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A 分店", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-17T00:00:00Z")
    w.upsert_cameras(nvra, {
        "d1": {"name": "大門", "connection_state": "CONNECTED"},
    })
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "d1",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-17T00:00:00Z",
    }])
    # image_health_check（用手動 SQL）
    conn = w._require_active()
    cur = conn.execute(
        """
        INSERT INTO image_health_checks
            (camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("d1", nvra, "2026-07-17T00:00:30Z",
         json.dumps({"blur_var": 12.5, "mean_luma": 0.92, "is_overexposed": True, "is_frozen": False}),
         json.dumps(["overexposed"])),
    )
    w.finish_scan_run(rid, finished_at="2026-07-17T00:01:00Z", status="partial", stats={})

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path
    del w, app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def client(detail_app):
    app, _ = detail_app
    return app.test_client()


# === /devices/<id> 測試 ===
def test_device_detail_returns_200(client):
    assert client.get("/devices/d1").status_code == 200


def test_device_detail_404_for_missing(client):
    assert client.get("/devices/does_not_exist").status_code == 404


def test_device_detail_shows_basic_info(client):
    body = client.get("/devices/d1").get_data(as_text=True)
    assert "大門" in body
    assert "A 分店" in body
    assert "10.0.0.1" in body
    assert "d1" in body


def test_device_detail_shows_chinese_event(client):
    body = client.get("/devices/d1").get_data(as_text=True)
    assert "影像訊號斷線（黑畫面）" in body


def test_device_detail_shows_image_health_metrics(client):
    body = client.get("/devices/d1").get_data(as_text=True)
    assert "blur_var" in body or "模糊度" in body
    assert "平均亮度" in body


def test_device_detail_links_to_health_history(client):
    body = client.get("/devices/d1").get_data(as_text=True)
    assert "/health/cameras/d1" in body


def test_device_detail_no_recording_status_block_when_no_data(client):
    """沒 recording_status 紀錄時不顯示「缺段小時數」卡片（標題卡還是會出現但內容為 alert）。"""
    body = client.get("/devices/d1").get_data(as_text=True)
    # 「缺段小時數」label 只在有資料時才出現
    assert "缺段小時數" not in body


def test_device_detail_shows_recording_status_when_present(detail_app):
    """有 recording_status 紀錄時應顯示完整率 + 缺段小時數。"""
    app, db_path = detail_app
    # 把 recording_status 寫入
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO recording_status "
        "(nvr_id, camera_id, window_start, window_end, completeness, missing_seconds, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "d1", "2026-07-28T00:00:00Z", "2026-07-29T00:00:00Z",
         0.65, 12.6 * 3600, "2026-07-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    c = app.test_client()
    body = c.get("/devices/d1").get_data(as_text=True)
    assert "24h 完整率" in body
    assert "65.0%" in body
    assert "12.6" in body


# === /health/cameras/<id> 測試 ===
def test_health_history_returns_200(client):
    assert client.get("/health/cameras/d1").status_code == 200


def test_health_history_404_for_missing(client):
    assert client.get("/health/cameras/nope").status_code == 404


def test_health_history_shows_record(client):
    body = client.get("/health/cameras/d1").get_data(as_text=True)
    assert "2026-07-17T00:00:30Z" in body


def test_health_history_shows_metrics(client):
    body = client.get("/health/cameras/d1").get_data(as_text=True)
    # blur_var 顯示
    assert "12.5" in body


def test_health_history_shows_flags(client):
    body = client.get("/health/cameras/d1").get_data(as_text=True)
    assert "overexposed" in body


def test_health_history_shows_empty_state():
    """無 image_health_checks 紀錄時顯示提示。"""
    import tempfile, gc
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        w = SqliteWriter(db_path)
        nvra = w.upsert_nvr({"id":"X","name":"X","host":"1.1.1.1","port":8443,"username":"u","password":"p"})
        rid = w.begin_scan_run("2026-07-17T00:00:00Z")
        w.upsert_cameras(nvra, {"d99": {"name":"cam99","connection_state":"CONNECTED"}})
        w.finish_scan_run(rid, finished_at="2026-07-17T00:01:00Z", status="partial", stats={})
        # close conn before creating new app
        w._conn.close()

        app2 = create_app(db_path=db_path)
        c2 = app2.test_client()
        body = c2.get("/health/cameras/d99").get_data(as_text=True)
        assert "尚無" in body or "NVR_IMAGE_HEALTH" in body
        del app2, c2, w
        gc.collect()
    finally:
        Path(db_path).unlink(missing_ok=True)
