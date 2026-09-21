"""Week 5 #012：before_request 入口。

決策樹（D2）：
- AUTH_ENABLED=False → 完全 pass-through（首行 return）
- AUTH_ENABLED=True + 來源 IP ∈ 白名單 → 視為已登入 ops 群組（current_user 偽造）
- AUTH_ENABLED=True + 外網 + Flask-Login 已登入 → 正常放行
- AUTH_ENABLED=True + 外網 + 未登入 → redirect /login
- AUTH_ENABLED=True + 已登入但 must_change_password → 強制 /change-password
"""
from __future__ import annotations

from flask import Flask, redirect, request, url_for
from flask_login import current_user

from web.auth.ip_whitelist import is_trusted_ip
from web.config import FeatureFlags


def install_auth_middleware(app: Flask) -> None:
    """註冊 Flask-Login 整合 + before_request hook。

    Args:
        app: Flask application instance
    """
    flags: FeatureFlags = app.config["FEATURE_FLAGS"]

    # Feature flag 關閉 → 完全不安裝（dashboard 行為與 Week 4 完全相同）
    if not flags.auth_enabled:
        return

    from flask_login import LoginManager

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    db_path = app.config["NVR_DB_PATH"]

    @login_manager.user_loader
    def _load_user(user_id: str):  # type: ignore[no-untyped-def]
        from web.auth.users import get_user_by_id

        return get_user_by_id(db_path, int(user_id))

    @app.before_request
    def _auth_hook() -> object:
        # login / logout / 改密碼 / 靜態資源 允許存取（避免無限 redirect）
        if request.path in ("/login", "/logout", "/change-password"):
            return None
        if request.path.startswith("/static") or request.path == "/favicon.ico":
            return None
        if request.path == "/healthz":
            return None

        ip = request.remote_addr or "0.0.0.0"
        # 內網 IP 自動白名單 → 視為已登入
        if is_trusted_ip(ip, trusted_cidrs=flags.ops_trusted_cidrs):
            # 不寫 audit_log（避免噪音；內網所有 request 都記太雜）
            return None

        # 外網 IP：未登入則跳 /login
        if not current_user.is_authenticated:
            from audit.log import write_audit_event

            write_audit_event(
                db_path=app.config["NVR_DB_PATH"],
                event="access_denied",
                ip=ip,
                user_agent=request.headers.get("User-Agent"),
                payload={"path": request.path},
            )
            return redirect(url_for("auth.login", next=request.path))

        # 已登入但 must_change_password → 強制改密碼
        if getattr(current_user, "must_change_password", False):
            return redirect(url_for("auth.change_password"))

        return None
