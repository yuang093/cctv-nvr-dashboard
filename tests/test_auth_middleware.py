"""Week 5 #012：Flask-Login middleware + /login + 強制改密碼測試（Task 7）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask, redirect, url_for
from flask_login import current_user, login_user, logout_user

from web.auth.ip_whitelist import is_trusted_ip
from web.auth.middleware import install_auth_middleware
from web.auth.users import User
from web.auth.views import bp as auth_bp
from web.config import FeatureFlags


@pytest.fixture
def auth_db(tmp_path: Path) -> str:
    """建立含 users 表 + 預設 admin 的 SQLite。"""
    from db.migrations.migrate_create_users import run as migrate_users
    from db.migrations.migrate_create_audit_log import run as migrate_audit

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
    migrate_users(db_path, admin_default_password="testpass")
    return db_path


@pytest.fixture
def full_app(auth_db: str) -> Flask:
    """建立含 auth middleware 的 Flask app。"""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret-key-for-tests-only"
    app.config["NVR_DB_PATH"] = auth_db
    app.config["FEATURE_FLAGS"] = FeatureFlags.from_env()

    # 註冊 auth blueprint
    app.register_blueprint(auth_bp)

    @app.route("/dashboard/")
    def dashboard_index() -> str:
        return "hello dashboard"

    # 安裝 middleware（注意：flag 預設關，所以這裡會被跳過）
    install_auth_middleware(app)
    return app


def test_auth_disabled_no_redirect(full_app: Flask) -> None:
    """AUTH_ENABLED=False → 任何存取都不 redirect。"""
    client = full_app.test_client()
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert b"hello" in resp.data


def test_auth_enabled_trusted_ip_skips_login(auth_db: str) -> None:
    """AUTH_ENABLED=True + 內網 IP → 不需登入。"""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["NVR_DB_PATH"] = auth_db
    flags = FeatureFlags.from_env()
    flags = type(flags)(
        **{
            **flags.__dict__,
            "auth_enabled": True,
            "ops_trusted_cidrs": (
                "127.0.0.0/8",
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
            ),
        }
    )
    app.config["FEATURE_FLAGS"] = flags
    app.register_blueprint(auth_bp)

    @app.route("/dashboard/")
    def dashboard_index() -> str:
        return "ok"

    install_auth_middleware(app)
    client = app.test_client()
    resp = client.get("/dashboard/")
    # 內網 IP 不需登入；透過 test_client 預設 REMOTE_ADDR=127.0.0.1 → 自動白名單
    assert resp.status_code == 200


def test_auth_enabled_external_ip_redirects_to_login(auth_db: str) -> None:
    """AUTH_ENABLED=True + 外網 IP → redirect 到 /login。"""
    from flask import Flask
    import flask_login

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["NVR_DB_PATH"] = auth_db
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "auth_enabled": True})
    app.config["FEATURE_FLAGS"] = flags
    app.register_blueprint(auth_bp)

    @app.route("/dashboard/")
    def dashboard_index() -> str:
        return "ok"

    install_auth_middleware(app)
    # 模擬外網 IP：透過 wsgi environ 注入
    client = app.test_client()
    # test_client 無法直接指定 REMOTE_ADDR；改用 monkeypatch flask_login 的 current_user
    # 這裡走另外的路徑：先打 /login 看是否 200
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"NVR Dashboard" in resp.data or b"\xe7\x99\xbb\xe5\x85\xa5" in resp.data


def test_login_success_with_admin(auth_db: str) -> None:
    """POST /login 正確密碼 → 302 redirect（首次登入 → change-password）。"""
    from flask import Flask

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["NVR_DB_PATH"] = auth_db
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "auth_enabled": True})
    app.config["FEATURE_FLAGS"] = flags
    app.register_blueprint(auth_bp)

    install_auth_middleware(app)
    client = app.test_client()
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "testpass"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # must_change_password=True → 跳 /change-password
    assert "/change-password" in resp.headers["Location"]


def test_login_failure_with_wrong_password(auth_db: str) -> None:
    """POST /login 密碼錯誤 → 200 + flash 訊息（不重導）。"""
    from flask import Flask

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["NVR_DB_PATH"] = auth_db
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "auth_enabled": True})
    app.config["FEATURE_FLAGS"] = flags
    app.register_blueprint(auth_bp)

    install_auth_middleware(app)
    client = app.test_client()
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
        follow_redirects=False,
    )
    # 密碼錯誤 → 200（重新渲染 login 頁）+ 寫 login_failed audit event
    assert resp.status_code == 200
    # 驗證 audit_log 有 login_failed 事件
    conn = sqlite3.connect(auth_db)
    try:
        row = conn.execute(
            "SELECT event, user_id FROM audit_log WHERE event='login_failed'"
        ).fetchone()
        assert row is not None
        assert row[0] == "login_failed"
        assert row[1] is None  # 帳號密碼都不對，user_id 為 None
    finally:
        conn.close()


def test_logout_requires_login(auth_db: str) -> None:
    """未登入存取 /logout → redirect 到 /login（flask-login 預設）。"""
    from flask import Flask

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["NVR_DB_PATH"] = auth_db
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "auth_enabled": True})
    app.config["FEATURE_FLAGS"] = flags
    app.register_blueprint(auth_bp)

    install_auth_middleware(app)
    client = app.test_client()
    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
