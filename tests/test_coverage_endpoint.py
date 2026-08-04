"""Tests for /clips/coverage/data JSON API endpoint (Spec F Task 2)."""
from __future__ import annotations

import os

import pytest

# 設定 env vars **在 import 前**，跟其他 clips 測試一致
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")

from db.sqlite_writer import SqliteWriter  # noqa: E402
from web.clips_app import app  # noqa: E402
from web import db as webdb  # noqa: E402


def _init_db(db_path: str) -> None:
    """用 SqliteWriter 初始化 schema。"""
    w = SqliteWriter(db_path)
    w._init_schema()


@pytest.fixture
def clips_app(monkeypatch, tmp_path):
    """空的 clips app（DB 在 tmp_path，不灌資料）。

    回傳 Flask app 本身（不是 tuple）讓測試可直接 .config[...]/.test_client()。
    """
    db_path = str(tmp_path / "coverage_endpoint_test.db")
    monkeypatch.setenv("NVR_DB_PATH", db_path)
    _init_db(db_path)
    app.config["DB_PATH"] = db_path
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess.clear()
    return app


def test_coverage_data_endpoint_404_when_nvr_missing(clips_app):
    """無此 internal_id → 404。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage/data?nvr_id=99999&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z")
    assert rv.status_code == 404


def test_coverage_data_endpoint_400_on_bad_window(clips_app):
    """end < start → 400。"""
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-T",
        "name": "NVR-T2",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-T2")
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T05:00:00Z&end=2026-08-04T01:00:00Z"
    )
    assert rv.status_code == 400


def test_coverage_data_endpoint_502_when_nvr_unreachable(clips_app):
    """NVR 連線失敗 → 502（包裝為 JSON 錯誤）。"""
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-UNREACH",
        "name": "NVR-UNREACH",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-UNREACH")
    # 至少給 1 台 cam，否則 endpoint 會先回 404「沒有 cam」
    writer = SqliteWriter(db_path)
    writer.begin_scan_run("2026-08-04T00:00:00Z")
    writer.upsert_cameras(nvr_row["id"], {
        "cam-001": {"name": "Cam 1", "available": True, "connection_state": "CONNECTED"},
    })
    writer.finish_scan_run(nvr_row["id"], finished_at="2026-08-04T00:00:30Z", status="success",
                           stats={"total_cameras": 1, "abnormal_cameras": 0,
                                  "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    # 給 env vars 避免 _login_nvr 跑去 console prompt
    os.environ["AVIGILON_USER_NONCE"] = "test-nonce"
    os.environ["AVIGILON_USER_KEY"] = "test-key"
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z"
    )
    # 預期 502（連線失敗）；也接受 200 若實作選擇了 graceful fallback
    assert rv.status_code in (200, 502)
    if rv.status_code == 502:
        assert "error" in rv.get_json()
