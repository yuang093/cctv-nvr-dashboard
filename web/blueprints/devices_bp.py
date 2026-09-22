"""Week 6 #017 — devices_bp。

URL prefix: `/devices`, `/health`, `/trends`, `/events`, `/wall`, `/abnormal`
路由（12 條）：
    `/wall`                                 GET   相機牆視覺化
    `/devices`                              GET   跨 NVR 設備總覽
    `/devices/discover`                     GET/POST  CIDR 探索
    `/devices/discover/<int:session_id>`    GET   探索結果
    `/devices/<device_id>`                  GET   單台 cam 詳情
    `/health/cameras/<device_id>`           GET   cam 健康歷史
    `/trends`                               GET   趨勢 sparkline（Spec G）
    `/events`                               GET   事件列表（Phase 1 status 篩選）
    `/abnormal`                             GET   故障相機彙總
    `/abnormal/export.pdf`                  GET   故障 PDF
"""
from __future__ import annotations

from typing import cast

from flasgger import swag_from

import time

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    url_for,
)

from web import db as webdb
from web.app import _build_abnormal_pdf, _safe_int


def _db_path() -> str:
    return cast(str, current_app.config["DB_PATH"])


devices_bp = Blueprint("devices", __name__)


@devices_bp.route("/wall")
@swag_from("web/openapi/dashboard/devices_wall.yml")
def wall():
    """相機牆視覺化 grid（含縮圖 + status 圓點 + 計數 tab）。

    ?filter=all|online|signal_lost|no_signal（預設 all）
    """
    filter_kind = request.args.get("filter", "all")
    if filter_kind not in ("all", "online", "signal_lost", "no_signal"):
        filter_kind = "all"
    cams = webdb.get_wall_cameras_with_snapshots(_db_path(), filter_kind=filter_kind)
    counts = webdb.get_wall_filter_counts(_db_path())
    return render_template(
        "wall.html",
        cams=cams,
        filter_kind=filter_kind,
        counts=counts,
    )


@devices_bp.route("/devices")
@swag_from("web/openapi/dashboard/devices_list.yml")
def devices_list():
    """跨 NVR 設備總覽表。"""
    nvr_filter = request.args.get("nvr", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = max(1, int(request.args.get("page", "1") or "1"))
    per_page = 50
    rows, total = webdb.get_devices_paginated(
        _db_path(),
        page=page,
        per_page=per_page,
        nvr_filter=nvr_filter,
        status_filter=status_filter,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    nvrs = webdb.get_nvrs(_db_path())
    return render_template(
        "devices_list.html",
        rows=rows,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        nvr_filter=nvr_filter,
        status_filter=status_filter,
        nvrs=nvrs,
    )


@devices_bp.route("/devices/discover", methods=["GET", "POST"])
@swag_from("web/openapi/dashboard/devices_discover.yml")
def devices_discover():
    """CIDR 探索介面 + 探索網段執行。"""
    if request.method == "POST":
        cidr = (request.form.get("cidr") or "").strip()
        port = int(request.form.get("port") or "8443")
        if not cidr:
            return (
                render_template(
                    "discover.html",
                    error="請輸入 CIDR（例如 192.168.0.0/24）",
                    cidr=cidr,
                    port=port,
                ),
                400,
            )
        from web.discover import expand_cidr

        try:
            expand_cidr(cidr)
        except ValueError as e:
            return (
                render_template(
                    "discover.html",
                    error=str(e),
                    cidr=cidr,
                    port=port,
                ),
                400,
            )
        try:
            session_id = webdb.create_discover_session(
                _db_path(),
                cidr=cidr,
                port=port,
            )
        except Exception as e:
            return (
                render_template(
                    "discover.html",
                    error=str(e),
                    cidr=cidr,
                    port=port,
                ),
                400,
            )
        # 背景執行 CIDR probe
        from web.app import _start_probe_thread

        _start_probe_thread(_db_path(), session_id)
        return redirect(url_for("devices.devices_discover_result", session_id=session_id))
    return render_template("discover.html", cidr="192.168.0.0/24", port=8443)


@devices_bp.route("/devices/discover/<int:session_id>")
@swag_from("web/openapi/dashboard/devices_discover_detail.yml")
def devices_discover_result(session_id: int):
    """顯示單次探索 session 結果。

    Week 6 #017 Stage B：原本 web/app.py 用 `session=sess`，
    模板內 `{{ session.cidr }}` 引用。沿用相同變數名避免改 template。
    """
    sess = webdb.get_discover_session(_db_path(), session_id)
    if not sess:
        abort(404)
    return render_template("discover_result.html", session=sess)


@devices_bp.route("/devices/<device_id>")
@swag_from("web/openapi/dashboard/devices_detail.yml")
def device_detail(device_id: str):
    """單台 cam 詳情 + 影像健康卡。"""
    info = webdb.get_device_detail(_db_path(), device_id)
    if not info:
        abort(404)
    return render_template("device_detail.html", cam=info)


@devices_bp.route("/health/cameras/<device_id>")
@swag_from("web/openapi/dashboard/devices_health_api.yml")
def camera_health_history(device_id: str):
    """單台 cam 健康歷史（image_health_checks）。"""
    info = webdb.get_device_detail(_db_path(), device_id)
    if not info:
        abort(404)
    history = webdb.get_camera_health_history(_db_path(), device_id, limit=50)
    return render_template(
        "camera_health.html",
        cam=info,
        history=history,
    )


@devices_bp.route("/trends")
@swag_from("web/openapi/dashboard/devices_trends.yml")
def trends():
    """Cam 健康趨勢總覽（mini sparkline grid）。

    Query params: range / nvr_id / status / cam_id（Spec G）。
    """
    from web.trends import get_all_cams_health_summary

    range_str = request.args.get("range", "24h")
    range_hours = 24 if range_str == "24h" else (168 if range_str == "7d" else 24)
    nvr_filter = request.args.get("nvr_id") or None
    status_filter = request.args.get("status", "any")
    if status_filter not in ("any", "abnormal_only"):
        status_filter = "any"
    focus_cam_id = request.args.get("cam_id") or None

    summaries = get_all_cams_health_summary(
        _db_path(),
        range_hours=range_hours,
        nvr_filter=nvr_filter,
        status_filter=status_filter,
    )
    try:
        nvrs = webdb.list_enabled_nvrs(_db_path())
    except Exception:
        nvrs = []

    return render_template(
        "trends.html",
        summaries=summaries,
        range_hours=range_hours,
        nvrs=nvrs,
        current_nvr=nvr_filter,
        current_status=status_filter,
        focus_cam_id=focus_cam_id,
    )


@devices_bp.route("/events")
@swag_from("web/openapi/dashboard/devices_events.yml")
def events_list():
    """事件列表（hours / nvr_id / topic / status 篩選）。"""
    hours = _safe_int(request.args.get("hours"), 24, min_val=1, max_val=8760)
    nvr_id = request.args.get("nvr_id", type=int)
    topic = request.args.get("topic") or None
    status = request.args.get("status", "all")
    if status not in ("all", "open", "resolved"):
        status = "all"
    events = webdb.get_events_filtered(
        _db_path(),
        hours=hours,
        nvr_id=nvr_id,
        topic=topic,
        status=status,
        limit=200,
    )
    all_topics = webdb.get_all_topics(_db_path())
    all_nvrs = webdb.get_nvrs(_db_path())
    return render_template(
        "events_list.html",
        events=events,
        hours=hours,
        nvr_id=nvr_id,
        topic=topic or "",
        status=status,
        all_topics=all_topics,
        all_nvrs=all_nvrs,
        topic_zh=webdb.get_topic_zh,
    )


@devices_bp.route("/abnormal")
@swag_from("web/openapi/dashboard/devices_abnormal.yml")
def abnormal_list():
    """故障攝影機總覽（按相機分組）。"""
    groups = webdb.get_abnormal_cameras_grouped(_db_path())
    total_open = sum(g["open_count"] for g in groups)
    affected_nvrs = len({g["nvr_id"] for g in groups})
    return render_template(
        "abnormal.html",
        groups=groups,
        total_cameras=len(groups),
        total_open=total_open,
        affected_nvrs=affected_nvrs,
        topic_zh=webdb.get_topic_zh,
        topic_zh_map=webdb.ABNORMAL_TOPIC_ZH,
        last_run=webdb.get_last_scan_run(_db_path()),
    )


@devices_bp.route("/abnormal/export.pdf")
@swag_from("web/openapi/dashboard/devices_abnormal_export_pdf.yml")
def abnormal_export_pdf():
    """故障報告 PDF（繁體中文，reportlab 內嵌字型）。"""
    groups = webdb.get_abnormal_cameras_grouped(_db_path())
    topic_zh = webdb.get_topic_zh
    last_run = webdb.get_last_scan_run(_db_path())

    pdf_bytes = _build_abnormal_pdf(groups, topic_zh, last_run)
    ts = time.strftime("%Y%m%d_%H%M%S")
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="nvr_abnormal_report_{ts}.pdf"'
            ),
        },
    )
