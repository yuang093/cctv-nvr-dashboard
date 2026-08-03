"""
tests/test_discover_route.py
============================
Phase 2.8（Arisan）Phase #5：/devices/discover 探索網段表單 + session stub。
Phase #6 完整 CIDR 探測留待下階段。
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def discover_app(monkeypatch):
    # 阻止 route 啟動的 background thread（避免 race with fixture teardown）
    # 注意：不能 monkeypatch threading.Thread.start（會破壞 ThreadPoolExecutor）
    import web.app as _app
    monkeypatch.setattr(_app, "_start_probe_thread", lambda *a, **kw: None)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
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
def client(discover_app):
    app, _ = discover_app
    return app.test_client()


# === GET 表單 ===
def test_discover_get_returns_200(client):
    assert client.get("/devices/discover").status_code == 200


def test_discover_get_shows_form(client):
    body = client.get("/devices/discover").get_data(as_text=True)
    assert "CIDR" in body
    assert "8443" in body
    assert "192.168.0.0/24" in body  # 預設值


# === POST 建立 session ===
def test_discover_post_creates_session(client, discover_app, monkeypatch):
    """Phase #6：POST 觸發 probe（mock requests.get 避免真連網）。"""
    from unittest.mock import MagicMock
    monkeypatch.setattr(
        "web.discover.requests.get",
        MagicMock(side_effect=ConnectionError("mocked-no-network")),
    )
    app, db_path = discover_app
    resp = client.post("/devices/discover", data={"cidr": "192.168.10.0/30", "port": "8443"})
    # 預期 redirect 302
    assert resp.status_code == 302
    # 檢查 DB 內有 session（mock probe 立刻完成 → status='completed'）
    from web import db as webdb
    sessions = webdb._connect(db_path).execute(
        "SELECT cidr, status FROM discover_sessions"
    ).fetchall()
    assert len(sessions) >= 1
    assert sessions[0]["cidr"] == "192.168.10.0/30"
    assert sessions[0]["status"] in ("completed", "running", "pending")


def test_discover_post_missing_cidr_shows_error(client):
    resp = client.post("/devices/discover", data={"cidr": "", "port": "8443"})
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "請輸入 CIDR" in body


def test_discover_result_renders(client, discover_app):
    """GET /devices/discover/<id> 顯示 session 結果頁。"""
    app, db_path = discover_app
    from web import db as webdb
    sid = webdb.create_discover_session(db_path, cidr="10.0.0.0/24", port=8443)
    resp = client.get(f"/devices/discover/{sid}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "10.0.0.0/24" in body


def test_discover_result_404(client):
    """不存在的 session id → 404。"""
    resp = client.get("/devices/discover/99999")
    assert resp.status_code == 404
