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


@pytest.fixture
def fleet_client(fleet_db):
    """建 Flask test client（沿用 test_wall_routes 風格）。"""
    clear_cache()
    app = create_app(db_path=fleet_db)
    app.config["TESTING"] = True
    return app.test_client()


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
def test_get_fleet_view_partial_failure_isolated(monkeypatch, fleet_db, caplog):
    """一台 NVR 的 helper 拋例外：該台 status='unknown'，其他台仍正常計數；warning 有 log。"""
    from web import fleet

    # 找 fixture 內 nvr_id（測試不要 hardcode 哪台是 1）
    real_nvrs = fleet.get_nvrs(fleet_db)
    target = real_nvrs[0]
    target_id = target["id"]
    other = real_nvrs[1]
    other_id = other["id"]

    def fake_helper(db_path, filter_kind="all", nvr_id=None):
        if nvr_id == target_id:
            raise RuntimeError("simulated DB error")
        # 成功台回傳 3 台 cam 全 online
        return [{"category": "online"} for _ in range(3)]

    monkeypatch.setattr(fleet, "get_wall_cameras_with_snapshots", fake_helper)
    clear_cache()

    with caplog.at_level("WARNING"):
        result = fleet.get_fleet_view(fleet_db, force_refresh=True)

    by_id = {n["nvr_id"]: n for n in result}
    assert len(result) == 2, "結果應仍有 2 台 NVR"
    assert by_id[target_id]["status"] == "unknown"
    assert by_id[target_id]["total"] == 0
    assert by_id[other_id]["status"] == "ok"
    assert by_id[other_id]["total"] == 3
    assert any("fleet view failed" in r.message for r in caplog.records), \
        "應記 warning log"


# === 8. cache 與 db_path 綁定 — 不同 db 不會回錯資料 ===
def test_get_fleet_view_cache_isolated_by_db_path(monkeypatch, fleet_db):
    """切到不同 db_path 不應回前一個 DB 的快取資料。"""
    from web import fleet

    # 在 fleet_db 先跑一次
    clear_cache()
    fleet.get_fleet_view(fleet_db, force_refresh=True)
    assert fleet._CACHE["db_path"] == fleet_db

    # 建另一個 DB（無 NVR）
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        other_db = f.name
    SqliteWriter(other_db)
    try:
        result = fleet.get_fleet_view(other_db)
        assert result == [], "切到另一個 DB 應重新計算（不是回前一個 DB 資料）"
        assert fleet._CACHE["db_path"] == other_db
    finally:
        Path(other_db).unlink(missing_ok=True)


# === 9. force_refresh=True 可繞過尚在 TTL 內的快取 ===
def test_get_fleet_view_force_refresh_bypasses_cache(monkeypatch):
    """TTL 內 force_refresh=True 仍應重算（呼叫次數增加）。"""
    from web import fleet

    # 建只有 1 台 NVR 的 DB（避免 count 計算被 fixture 干擾）
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    w.upsert_nvr({"id": "X", "name": "X", "host": "10.0.0.99",
                  "port": 8443, "username": "u", "password": "p"})
    del w
    gc.collect()

    clear_cache()
    t = [1000.0]
    monkeypatch.setattr(fleet.time, "time", lambda: t[0])

    call_count = [0]

    def counting(*args, **kwargs):
        call_count[0] += 1
        return []

    monkeypatch.setattr(fleet, "get_wall_cameras_with_snapshots", counting)

    fleet.get_fleet_view(db_path)
    assert call_count[0] == 1

    # TTL 內（25s）再呼叫，正常情況走 cache
    t[0] = 1025.0
    fleet.get_fleet_view(db_path)
    assert call_count[0] == 1, "TTL 內不應重算"

    # TTL 內但 force_refresh=True → 必須重算
    fleet.get_fleet_view(db_path, force_refresh=True)
    assert call_count[0] == 2, "force_refresh 應繞過快取"

    Path(db_path).unlink(missing_ok=True)


# === 10. status='degraded' 分支（只有 signal_lost，沒有 no_signal）===
def test_get_fleet_view_status_degraded_only_signal_lost():
    """有 signal_lost 但無 no_signal → status='degraded'。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-D", "name": "D 站", "host": "10.0.0.9",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "d1": {"name": "D1", "connection_state": "CONNECTED"},
        "d2": {"name": "D2", "connection_state": "CONNECTED"},
    })
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "d2",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-29T00:00:00Z",
    }])
    w.finish_scan_run(rid, finished_at="2026-07-29T00:01:00Z", status="success",
                      stats={"total_cameras": 2, "abnormal_cameras": 1,
                             "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    del w
    gc.collect()

    clear_cache()
    result = get_fleet_view(db_path, force_refresh=True)
    assert len(result) == 1
    assert result[0]["status"] == "degraded"
    assert result[0]["signal_lost"] == 1
    assert result[0]["no_signal"] == 0

    Path(db_path).unlink(missing_ok=True)


# === 11. enabled=0 的 NVR 必須被排除 ===
def test_get_fleet_view_excludes_disabled_nvrs():
    """enabled=0 的 NVR 不應出現在 /fleet 結果。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "ENABLED", "name": "啟用中", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    disabled_id = w.upsert_nvr({
        "id": "DISABLED", "name": "已停用", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    })
    # SqliteWriter 沒有 set_nvr_enabled：直接用 SQL 改 nvr_servers.enabled
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE nvr_servers SET enabled = 0 WHERE id = ?", (disabled_id,))
    conn.commit()
    conn.close()
    del w
    gc.collect()

    clear_cache()
    result = get_fleet_view(db_path, force_refresh=True)
    names = {n["name"] for n in result}
    assert "啟用中" in names
    assert "已停用" not in names, "停用的 NVR 不應出現"

    Path(db_path).unlink(missing_ok=True)


# === 12. /fleet route 回 200 並含 NVR 名稱 + summary ===
def test_fleet_route_returns_200_and_renders_nvr_names(fleet_client):
    resp = fleet_client.get("/fleet")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A 辦公室" in body
    assert "B 倉庫" in body
    # fixture 有 2 台 NVR / 8 台 cam（critical + signal_lost + no_signal + 健康）
    # 精確斷言：Task 4 替換模板時必須 conscious 改這行
    assert "共 2 台伺服器 / 8 台 cam" in body
