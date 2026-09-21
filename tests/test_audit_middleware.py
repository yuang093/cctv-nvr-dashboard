"""Week 5 #015：audit Flask middleware 測試（Task 3）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def audit_app(tmp_path: Path):
    """建立含 audit_log 表的 Flask app。"""
    from flask import Flask
    from audit.middleware import install_audit_hook
    from db.migrations.migrate_create_audit_log import run as migrate_audit
    from web.config import FeatureFlags

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """
    )
    conn.commit()
    conn.close()
    migrate_audit(db_path)

    app = Flask(__name__)
    app.config["NVR_DB_PATH"] = db_path
    return app, db_path


def test_audit_middleware_disabled_passes_through(audit_app) -> None:  # type: ignore[no-untyped-def]
    """AUDIT_ENABLED=False → middleware 不寫 audit_log。"""
    from flask import Flask
    from audit.middleware import install_audit_hook
    from web.config import FeatureFlags

    app, db_path = audit_app
    flags = FeatureFlags.from_env()  # 全 False
    app.config["FEATURE_FLAGS"] = flags
    install_audit_hook(app)

    @app.route("/probe")
    def probe() -> str:
        return "ok"

    client = app.test_client()
    resp = client.get("/probe")
    assert resp.status_code == 200
    # audit_log 應為空（middleware 完全 pass-through）
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_audit_middleware_records_request_when_enabled(audit_app) -> None:  # type: ignore[no-untyped-def]
    """AUDIT_ENABLED=True → middleware 寫一筆 'request' 事件。"""
    from flask import Flask
    from audit.middleware import install_audit_hook
    from web.config import FeatureFlags

    app, db_path = audit_app
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "audit_enabled": True})
    app.config["FEATURE_FLAGS"] = flags
    install_audit_hook(app)

    @app.route("/probe")
    def probe() -> str:
        return "ok"

    client = app.test_client()
    resp = client.get("/probe", headers={"User-Agent": "TestAgent/1.0"})
    assert resp.status_code == 200
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT event, ip, user_agent, payload_json FROM audit_log"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "request"
        assert rows[0][2] == "TestAgent/1.0"
        assert "/probe" in rows[0][3]
    finally:
        conn.close()


def test_audit_middleware_skips_static_paths(audit_app) -> None:  # type: ignore[no-untyped-def]
    """AUDIT_ENABLED=True → /static、/favicon.ico、/healthz 不記。"""
    from flask import Flask
    from audit.middleware import install_audit_hook
    from web.config import FeatureFlags

    app, db_path = audit_app
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "audit_enabled": True})
    app.config["FEATURE_FLAGS"] = flags
    install_audit_hook(app)

    @app.route("/healthz")
    def healthz() -> str:
        return "ok"

    @app.route("/static/test.css")
    def static_test() -> str:
        return "body {}"

    @app.route("/favicon.ico")
    def favicon() -> str:
        return ""

    client = app.test_client()
    client.get("/healthz")
    client.get("/static/test.css")
    client.get("/favicon.ico")
    # /healthz 雖然在 skip list 但也要讓請求通過
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert count == 0  # 全部被 skip
    finally:
        conn.close()
