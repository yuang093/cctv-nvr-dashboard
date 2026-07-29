"""
tests/test_fleet_view.py
=========================
2026-07-29 新功能：/fleet 頁伺服器概覽。

對應 spec：docs/superpowers/specs/2026-07-29-fleet-view-design.md
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app
from web.fleet import get_fleet_view, clear_cache


@pytest.fixture
def fleet_db():
    """灌 2 台 NVR、各 NVR 不同 cam 狀態（健康 / 訊號中斷 / 無訊號）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A 辦公室", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    nvrb = w.upsert_nvr({
        "id": "NVR-B", "name": "B 倉庫", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    # A：5 cam（3 健康 + 1 訊號中斷 + 1 無訊號）
    w.upsert_cameras(nvra, {
        "a1": {"name": "A1", "connection_state": "CONNECTED"},
        "a2": {"name": "A2", "connection_state": "CONNECTED"},
        "a3": {"name": "A3", "connection_state": "CONNECTED"},
        "a4": {"name": "A4", "connection_state": "CONNECTED"},
        "a5": {"name": "A5", "connection_state": "CONNECTED"},
    })
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "a4",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-29T00:00:00Z",
    }])
    w.insert_events(rid, nvra, [{
        "eventId": "e2", "deviceId": "a5",
        "eventTopics": ["STATE_LONG_FAILED"],
        "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-07-29T00:00:00Z",
    }])
    # B：3 cam 全健康
    w.upsert_cameras(nvrb, {
        "b1": {"name": "B1", "connection_state": "CONNECTED"},
        "b2": {"name": "B2", "connection_state": "CONNECTED"},
        "b3": {"name": "B3", "connection_state": "CONNECTED"},
    })
    w.finish_scan_run(rid, finished_at="2026-07-29T00:01:00Z", status="success",
                      stats={"total_cameras": 8, "abnormal_cameras": 2,
                             "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0})
    del w
    gc.collect()
    yield db_path
    Path(db_path).unlink(missing_ok=True)


# === 1. get_fleet_view 回傳 list，每台 NVR 一個 dict ===
def test_get_fleet_view_returns_one_dict_per_nvr(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all("name" in n and "total" in n for n in result)


# === 2. 每台 NVR 的 total = 該 NVR cam 總數 ===
def test_get_fleet_view_counts_total_cameras(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    by_name = {n["name"]: n for n in result}
    assert by_name["A 辦公室"]["total"] == 5
    assert by_name["B 倉庫"]["total"] == 3


# === 3. 每台 NVR 三類計數正確 ===
def test_get_fleet_view_counts_health_categories(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    by_name = {n["name"]: n for n in result}
    a = by_name["A 辦公室"]
    assert a["healthy"] == 3
    assert a["signal_lost"] == 1
    assert a["no_signal"] == 1
    b = by_name["B 倉庫"]
    assert b["healthy"] == 3
    assert b["signal_lost"] == 0
    assert b["no_signal"] == 0


# === 4. status 推論正確（critical / degraded / ok）===
def test_get_fleet_view_status_inference(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    by_name = {n["name"]: n for n in result}
    assert by_name["A 辦公室"]["status"] == "critical"
    assert by_name["B 倉庫"]["status"] == "ok"


# === 5. 0 台 NVR 回空 list ===
def test_get_fleet_view_empty_db_returns_empty_list():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    SqliteWriter(db_path)
    clear_cache()
    assert get_fleet_view(db_path) == []
    Path(db_path).unlink(missing_ok=True)


# === 6. 30s TTL 內走 cache ===
def test_get_fleet_view_uses_cache_within_ttl(monkeypatch, fleet_db):
    """兩次呼叫 get_fleet_view 30s 內，第二次的 timestamp 應等於第一次（走 cache）。"""
    from web import fleet

    clear_cache()
    t = [1000.0]
    monkeypatch.setattr(fleet.time, "time", lambda: t[0])

    fleet.get_fleet_view(fleet_db, force_refresh=True)
    cached_ts_after_first = fleet._CACHE["ts"]

    t[0] = 1025.0
    fleet.get_fleet_view(fleet_db)
    assert fleet._CACHE["ts"] == cached_ts_after_first

    t[0] = 1065.0
    fleet.get_fleet_view(fleet_db)
    assert fleet._CACHE["ts"] == 1065.0


# === 7. 單台 NVR 失敗隔離 ===
def test_get_fleet_view_partial_failure_isolated(monkeypatch, fleet_db):
    """一台 NVR 的 helper 拋例外，該台 status='unknown'，其他台仍正常。"""
    from web import fleet

    def boom(*args, **kwargs):
        if kwargs.get("nvr_id") == 1:
            raise RuntimeError("simulated DB error")
        return []

    monkeypatch.setattr(fleet, "get_wall_cameras_with_snapshots", boom)
    clear_cache()

    result = fleet.get_fleet_view(fleet_db, force_refresh=True)
    statuses = sorted(n["status"] for n in result)
    assert "unknown" in statuses
