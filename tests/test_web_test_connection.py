"""
tests/test_web_test_connection.py
=================================
Phase 2.5a+：「測試連線」AJAX endpoint 的整合測試。

涵蓋：
    - 缺 AVIGILON_USER_NONCE/KEY → 500
    - 缺欄位 → 400
    - 連線成功（mock scanner） → 200 + ok=true
    - 連線失敗（mock scanner） → 200 + ok=false
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def conn_app():
    """最小 Flask app（DB 用 tempfile，不灌資料 — 測試連線不碰 DB）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    # 必須有 1 台 NVR 才能讓其他測試不誤判「空 DB」→ 但測試連線不讀 DB
    # 留空 DB 也 OK
    del w
    gc.collect()
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path
    del app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def client(conn_app):
    app, _ = conn_app
    return app.test_client()


# === 1. 缺 env vars → 500 ===
def test_test_connection_missing_env_returns_500(client, monkeypatch):
    """未設定 AVIGILON_USER_NONCE/KEY → 500。"""
    monkeypatch.delenv("AVIGILON_USER_NONCE", raising=False)
    monkeypatch.delenv("AVIGILON_USER_KEY", raising=False)
    resp = client.post(
        "/nvrs/test-connection",
        json={
            "host": "1.2.3.4",
            "port": 8443,
            "username": "u",
            "password": "p",
        },
    )
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert "AVIGILON" in body["message"]


# === 2. 缺欄位 → 400 ===
def test_test_connection_missing_field_returns_400(client, monkeypatch):
    """缺 password → 400。"""
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test_nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test_key")
    resp = client.post(
        "/nvrs/test-connection",
        json={
            "host": "1.2.3.4",
            "port": 8443,
            "username": "u",
            # password 缺
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "password" in body["message"]


def test_test_connection_missing_host_returns_400(client, monkeypatch):
    """缺 host → 400。"""
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test_nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test_key")
    resp = client.post(
        "/nvrs/test-connection",
        json={
            # host 缺
            "port": 8443,
            "username": "u",
            "password": "p",
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# === 3. 連線成功（mock scanner.login） → 200 + ok=true ===
def test_test_connection_success_returns_ok(client, monkeypatch):
    """正常 NVR → 200 + ok=true。"""
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test_nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test_key")

    from web import app as webapp

    if not webapp._HAS_SCANNER:
        pytest.skip("nvr_scanner 模組未安裝")

    def fake_login(self):
        return "fake_session_token"

    monkeypatch.setattr(webapp.AvigilonScanner, "login", fake_login)

    resp = client.post(
        "/nvrs/test-connection",
        json={
            "host": "1.2.3.4",
            "port": 8443,
            "username": "u",
            "password": "p",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "latency" in body["message"]
    assert "latency_ms" in body
    assert body["latency_ms"] >= 0


# === 4. 連線失敗（mock scanner.login 拋例外） → 200 + ok=false ===
def test_test_connection_failure_returns_not_ok(client, monkeypatch):
    """連不上 → 200 + ok=false（不報 500，因為 login 失敗是預期場景）。"""
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test_nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test_key")

    from web import app as webapp

    if not webapp._HAS_SCANNER:
        pytest.skip("nvr_scanner 模組未安裝")

    def fake_fail(self):
        raise ConnectionError("test connection refused")

    monkeypatch.setattr(webapp.AvigilonScanner, "login", fake_fail)

    resp = client.post(
        "/nvrs/test-connection",
        json={
            "host": "1.2.3.4",
            "port": 8443,
            "username": "u",
            "password": "p",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert "連線失敗" in body["message"]
    assert "ConnectionError" in body["message"]


# === 5. 無效 JSON → 400 ===
def test_test_connection_invalid_json_returns_400(client, monkeypatch):
    """不是合法 JSON → 400。"""
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test_nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test_key")
    # 直接傳字串而非 JSON object
    resp = client.post(
        "/nvrs/test-connection", data="not json", content_type="text/plain"
    )
    assert resp.status_code == 400
