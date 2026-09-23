"""Week 6 #017 — dashboard_bp。

URL prefix: `/`
路由（5 條 + 1 dark toggle 共用）：
    `/`                  GET   首頁總覽（dashboard.html）
    `/theme`             GET   主題預覽
    `/theme/apply`       POST  套用主題
    `/fleet`             GET   跨 NVR 概覽
    `/dark/toggle`       POST  深色模式（與 8555 clips 雙方共有；共用 jinja filter 注入）

依賴：從 web.app 取 _count_online_cameras / _to_taipei_str 等 helper。
設計：用 `flask.current_app` 取代 closure 變數 `app`，行為等價於 `app.config["DB_PATH"]`。
"""
from __future__ import annotations

from typing import cast

from flasgger import swag_from

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)

from web import db as webdb
from web.fleet import (
    get_camera_health_distribution,
    get_fleet_view,
    get_system_health,
    get_thumbnail_coverage,
)

# Plan §D2：仍從 web.app 拉 helper。
from web.app import _count_online_cameras, _to_taipei_str


def _db_path() -> str:
    """等效於 web.app._get_db_path(app) — 從 current_app.config 取 DB_PATH。"""
    return cast(str, current_app.config["DB_PATH"])


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.context_processor
def _inject_theme():
    """把 session theme + dark 注入所有 template，讓 base.html 能讀到。"""
    return dict(
        theme=flask_session.get("theme", ""),
        dark=flask_session.get("dark", False),
    )


@dashboard_bp.app_template_filter("taipei")
def _taipei_filter(value):
    """Jinja filter: {{ e.detected_at | taipei }} 自動轉台灣時間。"""
    return _to_taipei_str(value)


@dashboard_bp.route("/dark/toggle", methods=["POST"])
@swag_from("web.openapi.dashboard.dashboard_dark_toggle.yml")
def dark_toggle():
    """切換深色模式。"""
    flask_session["dark"] = not flask_session.get("dark", False)
    return redirect(request.referrer or url_for("dashboard"))


@dashboard_bp.route("/")
@swag_from("web.openapi.dashboard.dashboard_index.yml")
def dashboard():
    stats = webdb.get_overall_stats(_db_path())
    # Phase 2.8（Arisan 磁磚點擊跳轉）：即時算線上 cam 數（GET 每台 NVR /cameras）
    stats["online_cameras"] = _count_online_cameras(_db_path(), timeout_per_nvr=5)
    recent = webdb.get_recent_runs(_db_path(), limit=5)
    top_missing = webdb.get_top_missing_cameras(_db_path(), limit=5)
    recording_latest_at = webdb.get_latest_recording_check_at(_db_path())
    return render_template(
        "dashboard.html",
        stats=stats,
        recent=recent,
        top_missing=top_missing,
        recording_latest_at=recording_latest_at,
    )


@dashboard_bp.route("/theme")
@swag_from("web.openapi.dashboard.dashboard_theme.yml")
def theme_preview():
    """主題選擇頁面（六種風格預覽）。"""
    current = flask_session.get("theme", "")
    return render_template("theme_preview.html", current=current)


@dashboard_bp.route("/theme/apply", methods=["POST"])
@swag_from("web.openapi.dashboard.dashboard_theme_apply.yml")
def theme_apply():
    """套用選擇的主題（寫入 session）。"""
    theme = request.form.get("theme", "")
    flask_session["theme"] = theme
    flash(f"主題已套用：{theme}", "success")
    return redirect(url_for("dashboard"))


@dashboard_bp.route("/fleet")
@swag_from("web.openapi.dashboard.dashboard_fleet.yml")
def fleet():
    """跨 NVR 伺服器概覽（總計 / 健康 / 異常 分類）。"""
    nvrs = get_fleet_view(_db_path())
    total_cams = sum(n["total"] for n in nvrs)
    return render_template(
        "fleet.html",
        nvrs=nvrs,
        total_cams=total_cams,
        health_dist=get_camera_health_distribution(_db_path()),
        thumb_cov=get_thumbnail_coverage(_db_path()),
        sys_health=get_system_health(),
    )
