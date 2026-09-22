"""Week 6 #017 — runs_bp。

URL prefix: `/runs`, `/reports`, `/query`
路由（5 條）：
    `/runs`                                GET  跑列表
    `/runs/<int:run_id>`                   GET  跑詳情
    `/reports`                             GET  報告列表
    `/reports/download/<int:run_id>`       GET  下載歷史 PDF
    `/query`                               GET/POST  ad-hoc 唯讀 SQL

依賴：web.db（get_paginated_runs / get_run / get_run_events / get_run_cameras /
      get_nvr_failures_for_run / get_topic_zh / list_reports / find_report / run_readonly_query）
"""
from __future__ import annotations

from typing import cast

from flasgger import swag_from

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    request,
    send_file,
)

from web import db as webdb
from web.app import _group_run_events_by_camera, _safe_int

runs_bp = Blueprint("runs", __name__)


def _db_path() -> str:
    return cast(str, current_app.config["DB_PATH"])


@runs_bp.route("/runs")
@swag_from("web/openapi/dashboard/runs_list.yml")
def runs_list():
    page = _safe_int(request.args.get("page"), 1, min_val=1)
    data = webdb.get_paginated_runs(_db_path(), page=page, per_page=20)
    return render_template("runs_list.html", **data)


@runs_bp.route("/runs/<int:run_id>")
@swag_from("web/openapi/dashboard/runs_detail.yml")
def run_detail(run_id: int):
    run = webdb.get_run(_db_path(), run_id)
    if not run:
        abort(404, f"找不到 scan_run_id={run_id}")
    events = webdb.get_run_events(_db_path(), run_id)
    cameras = webdb.get_run_cameras(_db_path(), run_id)
    nvr_failures = webdb.get_nvr_failures_for_run(_db_path(), run_id)
    grouped = _group_run_events_by_camera(events)
    return render_template(
        "run_detail.html",
        run=run,
        events=events,
        cameras=cameras,
        grouped=grouped,
        topic_zh=webdb.get_topic_zh,
        nvr_failures=nvr_failures,
    )


@runs_bp.route("/reports")
@swag_from("web/openapi/dashboard/runs_reports.yml")
def reports_list():
    """歷史 PDF 報告列表（每次掃描自動歸檔一份）。"""
    from web import report_archive

    items = report_archive.list_reports(_db_path())
    return render_template("reports_list.html", items=items)


@runs_bp.route("/reports/download/<int:run_id>")
@swag_from("web/openapi/dashboard/runs_reports_download.yml")
def reports_download(run_id: int):
    """下載指定 run_id 的歷史 PDF。"""
    from web import report_archive

    fpath = report_archive.find_report(_db_path(), run_id)
    if fpath is None or not fpath.exists():
        abort(404, description=f"找不到 run_id={run_id} 的歸檔報告")
    return send_file(
        fpath,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=fpath.name,
    )


@runs_bp.route("/query", methods=["GET", "POST"])
@swag_from("web/openapi/dashboard/runs_query.yml")
def adhoc_query():
    """ad-hoc 唯讀 SELECT 頁。

    GET：顯示表單 + 範例 SQL
    POST：執行並顯示結果
    """
    result = None
    error = None
    sql = ""
    example_sql = (
        "SELECT e.id, e.event_topic, e.device_id, "
        "datetime(e.occurred_at) AS occurred,\n"
        "       datetime(e.resolved_at) AS resolved\n"
        "FROM events e\n"
        "ORDER BY e.id DESC\n"
        "LIMIT 20;"
    )
    if request.method == "POST":
        sql = request.form.get("sql", "").strip()
        if not sql:
            error = "請輸入 SQL 查詢"
        else:
            try:
                result = webdb.run_readonly_query(_db_path(), sql, max_rows=500)
            except ValueError as exc:
                error = str(exc)
    return render_template(
        "query.html",
        sql=sql,
        example_sql=example_sql,
        result=result,
        error=error,
    )
