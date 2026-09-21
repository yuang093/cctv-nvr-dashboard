"""Week 6 #018 — clips_pages_bp。

URL: `/`, `/clips`, `/dark/toggle`
純頁面 alias（前端用）或 dark mode switch。

`/` 與 `/clips` 兩個 URL 都回 clips.html（前端兩條入口路徑）。
"""
from __future__ import annotations

import os

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)

pages_bp = Blueprint("clips_pages", __name__)


@pages_bp.route("/")
def index():
    return render_template("clips.html")


@pages_bp.route("/clips")
def clips_page():
    return render_template("clips.html")


@pages_bp.route("/dark/toggle", methods=["POST"])
def dark_toggle():
    """切換深色模式（純 server-side，redirect 回來源頁）。"""
    flask_session["dark"] = not flask_session.get("dark", False)
    return redirect(request.referrer or url_for("clips_pages.index"))


# 註冊 context processor 給所有 clips 模板注入 dark state + dashboard URL
@pages_bp.context_processor
def _inject_theme():
    return dict(dark=flask_session.get("dark", False))


@pages_bp.context_processor
def _inject_dashboard_url():
    """Spec G Batch C Task 11：注入 8444 dashboard URL 給 coverage.html JS 用。

    8555 clips_app 跟 8444 web.app 跨 port；coverage.html 不能用相對路徑 /trends
    （會打到 8555/trends，該路徑不存在）。

    從 env NVR_DASHBOARD_URL 讀（預設 http://127.0.0.1:8444）；可用於 LAN 部署時
    把 8444 host:port 換成對外網址。
    """
    base = os.environ.get("NVR_DASHBOARD_URL", "http://127.0.0.1:8444").rstrip("/")
    return dict(dashboard_url=base)
