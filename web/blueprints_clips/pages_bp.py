"""Week 6 #018 — clips_pages_bp。

URL: `/`, `/clips`, `/dark/toggle`
純頁面 alias（前端用）或 dark mode switch。

`/` 與 `/clips` 兩個 URL 都回 clips.html（前端兩條入口路徑）。

context_processor 移到 web/clips_app.py factory 內註冊（app 級別）
—— bp context processor 只套用該 bp 路由觸發的 template，但
coverage.html 透過 coverage_bp 渲染，須用 app context processor。
"""
from __future__ import annotations

from flask import (
    Blueprint,
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
