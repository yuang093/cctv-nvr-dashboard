"""
tests/test_web.py
==================
Web UI 路由 smoke test。

策略：用 tempfile 開檔案 DB → 灌測試資料 → 用 Flask test client 跑各路由 → 檢查 200 + 內容。
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def seeded_web_app():
    """建立 Flask app + 灌好測試資料的檔案 DB。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    # 灌 2 台 NVR
    w.upsert_nvr({
        "id": "NVR-A", "name": "A 分店", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
        "tags": ["branch", "taipei"],
    })
    w.upsert_nvr({
        "id": "NVR-B", "name": "B 分店", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
        "tags": ["branch", "taichung"],
    })
    # 灌 1 次 scan_run + 1 個 event
    nvra_id, nvrb_id = 1, 2
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.upsert_cameras(nvra_id, {
        "d1": {"name": "大門", "connection_state": "CONNECTED", "available": True},
        "d2": {"name": "停車場", "connection_state": "LONG_FAILED", "available": False},
    })
    w.upsert_cameras(nvrb_id, {
        "d10": {"name": "後門", "connection_state": "CONNECTED", "available": True},
    })
    w.insert_events(rid, nvra_id, [{
        "eventId": "e1", "deviceId": "d2",
        "eventTopics": ["STATE_LONG_FAILED"],
        "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-06-23T00:00:00Z",
    }])
    w.finish_scan_run(
        rid, finished_at="2026-06-23T00:01:00Z",
        status="partial",
        stats={
            "total_cameras": 3, "abnormal_cameras": 1,
            "total_nvrs": 2, "ok_nvrs": 1, "failed_nvrs": 1,
        },
    )
    # 灌第 2 次成功的 scan_run
    rid2 = w.begin_scan_run("2026-06-23T01:00:00Z")
    w.finish_scan_run(
        rid2, finished_at="2026-06-23T01:00:30Z",
        status="success",
        stats={
            "total_cameras": 3, "abnormal_cameras": 0,
            "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0,
        },
    )

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path

    # cleanup
    del w, app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass  # Windows file lock 偶爾，無視


@pytest.fixture
def client(seeded_web_app):
    app, _ = seeded_web_app
    return app.test_client()


# === 1. Dashboard 200 + 內容 ===
def test_dashboard_200(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "儀表板" in body
    assert "NVR 總數" in body
    assert "2" in body  # 總 NVR 數


# === 2. Runs list 200 + 分頁 ===
def test_runs_list_200(client):
    resp = client.get("/runs")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "掃描紀錄" in body
    # 兩次 run 都列出
    assert "#1" in body
    assert "#2" in body
    assert "部分成功" in body
    assert "成功" in body


def test_runs_list_pagination(client):
    resp = client.get("/runs?page=1")
    assert resp.status_code == 200


# === 3. Run detail 200 + 顯示 events/cameras ===
def test_run_detail_200(client):
    resp = client.get("/runs/1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "掃描詳情" in body
    assert "STATE_LONG_FAILED" in body
    assert "大門" in body or "停車場" in body  # camera name
    assert "異常事件" in body


def test_run_detail_404(client):
    resp = client.get("/runs/999")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "找不到" in body or "999" in body


# === 4. NVRs list ===
def test_nvrs_list_200(client):
    resp = client.get("/nvrs")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A 分店" in body
    assert "B 分店" in body
    assert "10.0.0.1" in body
    assert "taipei" in body  # tag


# === 5. Events list 過濾 ===
def test_events_list_200(client):
    resp = client.get("/events")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "異常事件" in body
    assert "STATE_LONG_FAILED" in body


def test_events_list_filter_by_topic(client):
    resp = client.get("/events?topic=STATE_")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body


def test_events_list_filter_by_hours(client):
    resp = client.get("/events?hours=720")  # 30 天
    assert resp.status_code == 200


def test_events_list_filter_by_nvr(client):
    resp = client.get("/events?nvr_id=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A 分店" in body


# === Phase 1 Step 3a: /events status filter (open / resolved / all) ===
def test_events_list_default_shows_all(seeded_web_app):
    """GET /events 不帶 status → status='all'，應看到原本的事件。"""
    app, _ = seeded_web_app
    c = app.test_client()
    resp = c.get("/events")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body


def test_events_list_filter_open(seeded_web_app):
    """status=open → 只顯示未 resolved 的事件（seed 進來的 STATE_LONG_FAILED 沒 resolved）。"""
    app, db_path = seeded_web_app
    c = app.test_client()
    resp = c.get("/events?status=open")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body
    assert "OPEN" in body, "應有 OPEN badge"


def test_events_list_filter_resolved(seeded_web_app):
    """seed 一筆已 resolved 的事件 → status=resolved 看得到，status=open 看不到。"""
    app, db_path = seeded_web_app
    # 手動灌一筆 resolved 事件
    from db.sqlite_writer import SqliteWriter
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO events (
            scan_run_id, nvr_id, event_id, device_id,
            event_topic, event_topics_json, occurred_at,
            detected_at, resolved_at, raw_json
        ) VALUES (1, 1, 'e-resolved', 'd2', 'TAMPERING', '["TAMPERING"]',
                  '2026-06-23T00:00:00Z', '2026-06-23T00:00:00Z',
                  '2026-06-23T00:30:00Z', '{}')
        """,
    )
    conn.commit()
    conn.close()
    c = app.test_client()

    # status=resolved 看到 e-resolved
    resp = c.get("/events?status=resolved")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "TAMPERING" in body
    assert "RESOLVED" in body, "應有 RESOLVED badge"

    # status=open + topic=TAMPERING 應該空（該 topic 已全部 resolved）
    resp2 = c.get("/events?status=open&topic=TAMPERING")
    body2 = resp2.get_data(as_text=True)
    assert "所選條件下無異常事件" in body2, (
        "TAMPERING 已 resolved，status=open + topic 應無事件"
    )

    # status=open 不傳 topic 仍看得到 STATE_LONG_FAILED
    resp3 = c.get("/events?status=open")
    body3 = resp3.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body3
    assert "OPEN" in body3


def test_events_list_invalid_status_falls_back_to_all(client):
    """status=bogus → fallback 到 all（避免炸 500）。"""
    resp = client.get("/events?status=bogus")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body


# === Phase 1 Step 3b: /query ad-hoc SELECT 頁 ===
def test_query_get_shows_form(client):
    """GET /query → 200 + 表單文字"""
    resp = client.get("/query")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Ad-hoc Query" in body
    assert "SELECT" in body
    assert "<textarea" in body


def test_query_select_returns_results(client):
    """POST /query 跑合法 SELECT → 顯示結果表"""
    resp = client.post("/query", data={
        "sql": "SELECT event_topic, device_id FROM events LIMIT 5",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body
    assert "共" in body and "筆" in body


def test_query_rejects_insert(client):
    """INSERT 必須被擋 → 顯示『禁止』錯誤，不回 500"""
    resp = client.post("/query", data={
        "sql": "INSERT INTO events (event_topic) VALUES ('x')",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body or "INSERT" in body
    assert "STATE_LONG_FAILED" not in body  # 沒真的被 insert


def test_query_rejects_update(client):
    resp = client.post("/query", data={
        "sql": "UPDATE events SET resolved_at='x'",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body


def test_query_rejects_delete(client):
    resp = client.post("/query", data={
        "sql": "DELETE FROM events",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body


def test_query_rejects_drop(client):
    resp = client.post("/query", data={
        "sql": "DROP TABLE events",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body


def test_query_rejects_pragma(client):
    """PRAGMA 也被擋（雖然 query_only 已擋，為 defense in depth）"""
    resp = client.post("/query", data={
        "sql": "PRAGMA table_info(events)",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body


def test_query_rejects_multi_statement(client):
    """中段分號（多 statement）被擋"""
    resp = client.post("/query", data={
        "sql": "SELECT 1; SELECT 2",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body


def test_query_rejects_non_select_kw(client):
    """不以 SELECT/WITH 開頭 → 擋"""
    resp = client.post("/query", data={
        "sql": "EXPLAIN SELECT * FROM events",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body or "只允許" in body


def test_query_accepts_with_cte(client):
    """WITH ... SELECT（CTE）應被視為合法 SELECT"""
    resp = client.post("/query", data={
        "sql": "WITH t AS (SELECT 1 AS n) SELECT * FROM t",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 不應該出現「禁止」「只允許」
    assert 'class="alert alert-danger"' not in body
    assert "card-header" in body


def test_query_strips_comments_and_runs(client):
    """註解應被 strip 後執行"""
    resp = client.post("/query", data={
        "sql": "-- 這是註解\nSELECT event_topic FROM events LIMIT 1",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "STATE_LONG_FAILED" in body
    assert 'class="alert alert-danger"' not in body


def test_query_empty_sql_rejected(client):
    """空字串 → 提示"""
    resp = client.post("/query", data={"sql": ""})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'class="alert alert-danger"' in body


# === 6. Static 資源 ===
def test_static_css_200(client):
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "background" in body or "NVR" in body
