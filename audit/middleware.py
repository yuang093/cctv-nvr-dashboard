"""Week 5 #015：Flask before_request hook，記錄每筆 request 到 audit_log。

Feature flag：NVR_AUDIT_ENABLED=False → 完全 pass-through（首行 return），
dashboard 行為與 Week 4 完全相同。
"""
from __future__ import annotations

from flask import Flask, request

from audit.log import write_audit_event
from web.config import FeatureFlags


def install_audit_hook(app: Flask) -> None:
    """註冊 audit before_request hook。

    Args:
        app: Flask application instance
    """
    flags: FeatureFlags = app.config["FEATURE_FLAGS"]

    @app.before_request
    def _audit_hook() -> None:
        # Feature flag 預設關閉 — 完全 pass-through
        if not flags.audit_enabled:
            return None
        # 靜態資源 / health check 不記（避免噪音）
        path = request.path or ""
        if (
            path == "/favicon.ico"
            or path == "/healthz"
            or path.startswith("/static/")
            or path.startswith("/static")
        ):
            return None
        write_audit_event(
            db_path=app.config["NVR_DB_PATH"],
            event="request",
            ip=request.remote_addr or "0.0.0.0",
            user_agent=request.headers.get("User-Agent"),
            payload={"path": request.path, "method": request.method},
        )
        return None
