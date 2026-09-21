"""Week 5 #014：flask-limiter 整合測試（Task 5）。

Flask 4.x 移除了 test_client 的 environ_overrides/environ_base 參數。
這裡改用 monkeypatch 攔截 flask_limiter.util.get_remote_address，
讓不同測試案例可以指定不同的來源 IP。
"""
from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

from web.config import FeatureFlags
from web.ratelimit import install_rate_limiter


@pytest.fixture
def base_app() -> Flask:
    """建立基本 Flask app + /login 路由。"""
    app = Flask(__name__)
    app.config["NVR_RATE_LIMIT_STORAGE_URI"] = "memory://"

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str:
        return "ok"

    return app


def _patch_remote_addr(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    """Monkeypatch flask_limiter.util.get_remote_address 回傳固定 IP。"""
    from flask_limiter import util as _util

    monkeypatch.setattr(_util, "get_remote_address", lambda: ip)


def test_rate_limit_disabled_no_429(base_app: Flask) -> None:
    """RATE_LIMIT_ENABLED=False → 打 200 次 /login 不會被擋。"""
    flags = FeatureFlags.from_env()
    base_app.config["FEATURE_FLAGS"] = flags
    install_rate_limiter(base_app)

    client = base_app.test_client()
    for _ in range(200):
        resp = client.get("/login")
        assert resp.status_code == 200


def test_rate_limit_enabled_blocks_external_ip(
    base_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RATE_LIMIT_ENABLED=True + 外網 IP → /login 第 11 次 429。"""
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "rate_limit_enabled": True})
    base_app.config["FEATURE_FLAGS"] = flags
    install_rate_limiter(base_app)

    _patch_remote_addr(monkeypatch, "8.8.8.8")

    limiter = base_app.extensions.get("nvr_limiter")
    exempt_when = base_app.extensions.get("nvr_limit_exempt_when")
    assert limiter is not None
    base_app.view_functions["login"] = limiter.limit(
        "10 per minute", exempt_when=exempt_when
    )(base_app.view_functions["login"])

    client = base_app.test_client()
    for _ in range(10):
        resp = client.get("/login")
        assert resp.status_code == 200
    resp = client.get("/login")
    assert resp.status_code == 429


def test_rate_limit_whitelist_ops_ip(
    base_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RATE_LIMIT_ENABLED=True + 內網 IP → 不被限流。"""
    flags = FeatureFlags.from_env()
    flags = type(flags)(
        **{
            **flags.__dict__,
            "rate_limit_enabled": True,
            "ops_trusted_cidrs": (
                "127.0.0.0/8",
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
            ),
        }
    )
    base_app.config["FEATURE_FLAGS"] = flags
    install_rate_limiter(base_app)

    _patch_remote_addr(monkeypatch, "192.168.1.5")

    limiter = base_app.extensions.get("nvr_limiter")
    exempt_when = base_app.extensions.get("nvr_limit_exempt_when")
    assert limiter is not None
    base_app.view_functions["login"] = limiter.limit(
        "10 per minute", exempt_when=exempt_when
    )(base_app.view_functions["login"])

    client = base_app.test_client()
    for _ in range(200):
        resp = client.get("/login")
        assert resp.status_code == 200


def test_rate_limit_different_external_ips_isolated(
    base_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不同外網 IP 各自有 bucket（透過 monkeypatch 切換）。"""
    flags = FeatureFlags.from_env()
    flags = type(flags)(**{**flags.__dict__, "rate_limit_enabled": True})
    base_app.config["FEATURE_FLAGS"] = flags
    install_rate_limiter(base_app)

    limiter = base_app.extensions.get("nvr_limiter")
    exempt_when = base_app.extensions.get("nvr_limit_exempt_when")
    assert limiter is not None
    base_app.view_functions["login"] = limiter.limit(
        "10 per minute", exempt_when=exempt_when
    )(base_app.view_functions["login"])

    client = base_app.test_client()

    # IP A 打 10 次 → 第 11 次 429
    _patch_remote_addr(monkeypatch, "1.1.1.1")
    for _ in range(10):
        assert client.get("/login").status_code == 200
    assert client.get("/login").status_code == 429

    # IP B 仍可正常
    _patch_remote_addr(monkeypatch, "2.2.2.2")
    assert client.get("/login").status_code == 200


def test_ip_whitelist_helper() -> None:
    """驗證 is_trusted_ip helper（Task 5 順帶測）。"""
    from web.auth.ip_whitelist import is_trusted_ip
    cidrs = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    assert is_trusted_ip("127.0.0.1", cidrs) is True
    assert is_trusted_ip("192.168.1.10", cidrs) is True
    assert is_trusted_ip("10.5.5.5", cidrs) is True
    assert is_trusted_ip("172.20.0.1", cidrs) is True
    assert is_trusted_ip("8.8.8.8", cidrs) is False
    assert is_trusted_ip("not-an-ip", cidrs) is False
