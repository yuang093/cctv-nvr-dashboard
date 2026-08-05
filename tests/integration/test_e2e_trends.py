"""web/app.py /trends route 整合測試（Flask test_client + 真 SQLite）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from web.app import create_app


@pytest.fixture
def flask_client(tmp_path: Path, monkeypatch):
    """8444 app + tmp DB。"""
    db_path = str(tmp_path / "trends.db")

    # 建最小 schema（含 spec G 需要的 3 張表 + 完整 nvr_servers 欄位以相容 list_enabled_nvrs）
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            host TEXT NOT NULL DEFAULT '',
            port INTEGER NOT NULL DEFAULT 8443,
            username TEXT,
            password TEXT,
            verify_ssl INTEGER NOT NULL DEFAULT 0,
            site_id TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            is_ghost INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE image_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            nvr_server_id INTEGER,
            checked_at_utc TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            flags_json TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    # 用 factory 建 app 並注入 db_path（與既有 test_e2e_web.py 風格一致）
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, db_path


class TestTrendsRoute:
    def test_route_returns_200(self, flask_client):
        """GET /trends → 200。"""
        client, _ = flask_client
        r = client.get("/trends")
        assert r.status_code == 200

    def test_default_range_is_24h(self, flask_client):
        """不帶 query → 24h 模式（page 含「Cam 健康趨勢」標題）。"""
        client, db_path = flask_client
        # seed 1 台 NVR + 1 cam
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'd-1', 'Cam1', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()

        r = client.get("/trends")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "Cam 健康趨勢" in body

    def test_invalid_range_falls_back_to_24h(self, flask_client):
        """?range=99 → 200（fallback 24h，不 500）。"""
        client, _ = flask_client
        r = client.get("/trends?range=99")
        assert r.status_code == 200

    def test_nvr_dropdown_lists_all_nvrs(self, flask_client):
        """template 內含所有 NVR 名（給 dropdown）。"""
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-b', 'ACC-9')")
        conn.commit()
        conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        assert "ACC-8" in body
        assert "ACC-9" in body
