"""
tests/test_devices_list_route.py
================================
Phase 2.8（Arisan）Phase #5：/devices 跨 NVR 設備總覽表 + 分頁 filter。

路由：GET /devices?nvr=&status=&page=
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def devices_app():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A 分店", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    nvrb = w.upsert_nvr({
        "id": "NVR-B", "name": "B 分店", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-17T00:00:00Z")
    w.upsert_cameras(nvra, {
        "d1": {"name": "大門", "connection_state": "CONNECTED"},
        "d2": {"name": "停車場", "connection_state": "LONG_FAILED"},
        "d3": {"name": "後門", "connection_state": "CONNECTED"},
    })
    w.upsert_cameras(nvrb, {
        "d10": {"name": "倉庫大門", "connection_state": "CONNECTED"},
    })
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "d2",
        "eventTopics": ["STATE_LONG_FAILED"], "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-07-17T00:00:00Z",
    }])
    w.insert_events(rid, nvra, [{
        "eventId": "e2", "deviceId": "d3",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"], "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-17T00:00:00Z",
    }])
    w.finish_scan_run(rid, finished_at="2026-07-17T00:01:00Z", status="partial",
                      stats={})

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
def client(devices_app):
    app, _ = devices_app
    return app.test_client()


# === 1. 200 + 全 cam 顯示 ===
def test_devices_list_returns_200(client):
    assert client.get("/devices").status_code == 200


def test_devices_list_shows_all_cams(client):
    body = client.get("/devices").get_data(as_text=True)
    assert "大門" in body
    assert "停車場" in body
    assert "後門" in body
    assert "倉庫大門" in body


# === 2. nvr filter ===
def test_devices_filter_by_nvr(client, devices_app):
    """?nvr=NVR-A 內部 id → 只顯示 A 分店的 cam。"""
    app, db_path = devices_app
    from web import db as webdb
    nvrs = webdb.get_nvrs(db_path)
    nvr_a_id = next(n["id"] for n in nvrs if n["nvr_id"] == "NVR-A")
    body = client.get(f"/devices?nvr={nvr_a_id}").get_data(as_text=True)
    assert "大門" in body
    assert "停車場" in body
    assert "後門" in body
    assert "倉庫大門" not in body


# === 3. status filter ===
def test_devices_filter_status_signal_lost(client):
    body = client.get("/devices?status=signal_lost").get_data(as_text=True)
    assert 'device_id: <code>d3</code>' in body
    assert 'device_id: <code>d2</code>' not in body


def test_devices_filter_status_no_signal(client):
    body = client.get("/devices?status=no_signal").get_data(as_text=True)
    assert 'device_id: <code>d2</code>' in body
    assert 'device_id: <code>d3</code>' not in body


def test_devices_filter_status_online(client):
    body = client.get("/devices?status=online").get_data(as_text=True)
    # d1 (A), d3 (A), d10 (B) 都 online，但 d3 因 DEVICE_VIDEO_SIGNAL_LOST 不在
    assert 'device_id: <code>d1</code>' in body
    assert 'device_id: <code>d10</code>' in body
    assert 'device_id: <code>d2</code>' not in body
    assert 'device_id: <code>d3</code>' not in body


# === 4. 分頁 ===
def test_devices_pagination_page_size_50(client):
    """預設 page size 50 → 4 筆全在第 1 頁。"""
    body = client.get("/devices").get_data(as_text=True)
    # 沒分頁 UI（只 1 頁）
    assert "page=2" not in body


# === 5. 中文事件顯示 ===
def test_devices_shows_chinese_event_label(client):
    body = client.get("/devices").get_data(as_text=True)
    assert "長期失敗（拔網路線）" in body   # d2 STATE_LONG_FAILED


# === 6. NVR 名稱下拉選單 ===
def test_devices_nvr_filter_dropdown(client):
    """filter form 應含 NVR 下拉選項。"""
    body = client.get("/devices").get_data(as_text=True)
    assert "A 分店" in body
    assert "B 分店" in body


# === 7. 跳轉到 /devices/<id> ===
def test_devices_link_to_device_detail(client):
    """每行有「詳情」按鈕 → /devices/<device_id>。"""
    body = client.get("/devices").get_data(as_text=True)
    assert "/devices/d1" in body
    assert "/devices/d2" in body
    assert "/devices/d3" in body
    assert "/devices/d10" in body
