"""
tests/test_dashboard_refresh_completeness.py
============================================
Dashboard「🔄 重整完整率」按鈕對應的 route：

  POST /dashboard/refresh-completeness          → 啟動 background thread 跑 timeline
  GET  /dashboard/refresh-completeness/status   → 查 refresh 進度

設計：
- 不依賴 NVR_TIMELINE env（直接呼叫 batch_scan._timeline_check_loop）
- 已在跑 → 409 conflict
- 跑完 → recording_status 表實際更新

mock 策略：fixture monkeypatch 兩個源頭：
  - `batch_scan._timeline_check_loop`：跳過真 NVR 連線，直接寫 DB
  - `nvr_scanner.AvigilonScanner`：避免真實 NVR 認證
"""

from __future__ import annotations

import gc
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def refresh_app(monkeypatch):
    """建立 Flask app + 1 台 NVR + 2 台 cam + 1 筆舊 recording_status。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr(
        {
            "id": "NVR-A",
            "name": "A 分店",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    rid = w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(
        nvr_int,
        {
            "d1": {"name": "cam1", "connection_state": "CONNECTED", "available": True},
            "d2": {"name": "cam2", "connection_state": "CONNECTED", "available": True},
        },
    )
    w.finish_scan_run(
        rid,
        finished_at="2026-07-30T00:01:00Z",
        status="complete",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )
    # 舊的 recording_status（模擬 17 小時前）— 需要新 scan_run transaction
    rid2 = w.begin_scan_run("2026-07-30T00:30:00Z")
    w.upsert_recording_status(
        nvr_int,
        "d1",
        window_start="2026-07-29T00:00:00Z",
        window_end="2026-07-30T00:00:00Z",
        completeness=0.5,
        missing_seconds=43200.0,
    )
    w.finish_scan_run(
        rid2,
        finished_at="2026-07-30T00:30:30Z",
        status="complete",
        stats={
            "total_cameras": 2,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )
    # 舊的 recording_status（模擬 17 小時前）
    # ^ 注意：upsert_recording_status 需 scan_run 內才寫；移到下面 rid2

    # mock AvigilonScanner（避免真連線）
    mock_scanner = MagicMock()
    mock_scanner.get_timeline.return_value = {"data": []}
    monkeypatch.setattr("nvr_scanner.AvigilonScanner", lambda *a, **k: mock_scanner)

    # mock _timeline_check_loop（直接寫 DB，不真抓 timeline）
    from datetime import datetime, timezone, timedelta

    def fake_timeline_loop(
        scanner, nvr_int_id, writer, *, window_hours=24, verbose=False
    ):
        now = datetime.now(timezone.utc)
        ws = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        we = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for cam_id, pct, miss_s in [("d1", 0.95, 1800.0), ("d2", 0.88, 4320.0)]:
            writer.upsert_recording_status(
                nvr_int_id,
                cam_id,
                window_start=ws,
                window_end=we,
                completeness=pct,
                missing_seconds=miss_s,
            )
        return {"checked": 2, "written": 2, "errors": []}

    monkeypatch.setattr("batch_scan._timeline_check_loop", fake_timeline_loop)

    # env vars for batch_scan auth
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test-nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test-key")
    monkeypatch.setenv("AVIGILON_INTEGRATION_ID", "")

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
def client(refresh_app):
    app, _ = refresh_app
    return app.test_client()


def _wait_until_done(client, timeout_sec=5.0):
    """輪詢 /status 直到 running=False 或 timeout。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        s = client.get("/dashboard/refresh-completeness/status").get_json()
        if not s.get("running"):
            return s
        time.sleep(0.05)
    return s  # 最後一次結果（running 仍是 True）


# === 1. POST 回 202 + JSON ===
def test_refresh_post_returns_202(client):
    resp = client.post("/dashboard/refresh-completeness")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["ok"] is True
    assert "started_at" in data


# === 2. GET /status 回 JSON（即使沒跑也有 running 欄位）===
def test_refresh_status_returns_state(client):
    resp = client.get("/dashboard/refresh-completeness/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "running" in data


# === 3. 跑完 recording_status 實際被更新 ===
def test_refresh_writes_recording_status(client, refresh_app):
    _, db_path = refresh_app
    resp = client.post("/dashboard/refresh-completeness")
    assert resp.status_code == 202

    status = _wait_until_done(client)
    assert status["running"] is False
    # mock 寫入 0.95 / 0.88，覆蓋舊的 0.5
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT camera_id, completeness FROM recording_status ORDER BY camera_id"
    ).fetchall()
    conn.close()
    assert dict(rows) == {"d1": 0.95, "d2": 0.88}


# === 4. 已在跑 → 409 ===
def test_refresh_409_when_already_running(client):
    resp1 = client.post("/dashboard/refresh-completeness")
    assert resp1.status_code == 202
    # 馬上 POST 第二次 → 應該 conflict
    resp2 = client.post("/dashboard/refresh-completeness")
    assert resp2.status_code == 409
    data = resp2.get_json()
    assert data["ok"] is False
    assert "error" in data


# === 5. GET / 405 / 405 on non-POST ===
def test_refresh_only_post(client):
    """GET /refresh-completeness 不支援（用 /status）。"""
    resp = client.get("/dashboard/refresh-completeness")
    assert resp.status_code == 405

    # PUT 也不支援
    resp2 = client.put("/dashboard/refresh-completeness")
    assert resp2.status_code == 405
