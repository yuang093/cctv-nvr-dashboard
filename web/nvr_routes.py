"""
web/nvr_routes.py
=================
Phase 2.7 補：NVR CRUD Blueprint（給 8555 clips app 用）。

從 `web/app.py:301-645` 的 NVR routes 抽出來，包成 Flask Blueprint。
這樣 clips app 也能獨立擁有 NVR 管理功能，不依賴 8444。

URL prefix: `/nvrs`
註冊：在 `clips_app.py` 內 `app.register_blueprint(nvr_bp)`

與 8444 的差別：
- 8444 的 routes 在 `_register_routes(app)` 內註冊；endpoint 名是 `nvrs_list`
- 8555 改用 Blueprint，endpoint 名是 `nvr.list`（template url_for 要加 prefix）
- `_get_db_path` 改用 `current_app.config["DB_PATH"]`（Blueprint 內沒 app 變數）
"""

from __future__ import annotations

from typing import cast

import csv
import io
import json
import os
import sys
import time
import traceback

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    url_for,
)

from web import db as webdb

# AvigilonScanner 改在 runtime 才 import（避免 module-level import 失敗時
# 把整個 Blueprint 廢掉，後續所有 NVR 路由都 500）。
# 詳見 test_connection() 內的 lazy import 與 ImportError 詳細訊息。


nvr_bp = Blueprint("nvr", __name__, url_prefix="/nvrs")


# === 共用 DB_PATH 取得 ===
def _get_db_path() -> str:
    return cast(str, current_app.config["DB_PATH"])


# === Form 解析 / 正規化 helpers ===
# 2026-07-13 Phase 3：抽到 web/nvr_crud.py 共用模組
from web.nvr_crud import (
    parse_nvr_form as _parse_nvr_form,
    parse_csv as _parse_csv,
    parse_json as _parse_json,
    detect_format as _detect_format,
    safe_int as _safe_int,
    CSV_FIELDS as _CSV_FIELDS,
)

# === Routes ===


@nvr_bp.route("/")
def list():
    """NVR 清單（含分頁 + 搜尋）。"""
    page = _safe_int(request.args.get("page"), 1, min_val=1)
    q = (request.args.get("q") or "").strip() or None
    data = webdb.get_nvrs_paginated(_get_db_path(), page=page, per_page=20, q=q)
    return render_template(
        "nvrs_list.html",
        nvrs=data["nvrs"],
        total=data["total"],
        page=data["page"],
        per_page=data["per_page"],
        total_pages=data["total_pages"],
        q=q or "",
    )


@nvr_bp.route("/<int:internal_id>/toggle", methods=["POST"])
def toggle_enabled(internal_id: int):
    """切換單台 NVR 的啟用狀態（不刪除資料）。"""
    nvr = webdb.get_nvr(_get_db_path(), internal_id)
    if nvr is None:
        abort(404, f"找不到 internal_id={internal_id}")
    new_enabled = not bool(nvr.get("enabled", 1))
    webdb.set_nvr_enabled(_get_db_path(), internal_id, new_enabled)
    flash(
        f"NVR「{nvr['name']}」已{'啟用' if new_enabled else '停用'}",
        "success" if new_enabled else "warning",
    )
    return redirect(request.referrer or url_for("nvr.list"))


@nvr_bp.route("/new", methods=["GET", "POST"])
def new():
    """新增 NVR。"""
    if request.method == "POST":
        try:
            nvr_data = _parse_nvr_form(request.form)
            nvr_data["password"] = request.form.get("password") or ""
            if not nvr_data["password"]:
                raise ValueError("密碼必填（新增時）")
            webdb.create_nvr(_get_db_path(), nvr_data)
            flash(f"已建立 NVR「{nvr_data['nvr_id']}」", "success")
            return redirect(url_for("nvr.list"))
        except ValueError as e:
            return render_template(
                "nvr_form.html",
                mode="new",
                nvr=request.form.to_dict(),
                error=str(e),
            )
    return render_template("nvr_form.html", mode="new", nvr={}, error=None)


@nvr_bp.route("/<int:nvr_id>/edit", methods=["GET", "POST"])
def edit(nvr_id: int):
    """編輯 NVR。密碼留空=不變更。"""
    if request.method == "POST":
        try:
            nvr_data = _parse_nvr_form(request.form)
            password = request.form.get("password") or ""
            password_changed = bool(password)
            if password_changed:
                nvr_data["password"] = password
            webdb.update_nvr(
                _get_db_path(),
                nvr_id,
                nvr_data,
                password_changed=password_changed,
            )
            flash(f"已更新 NVR「{nvr_data['nvr_id']}」", "success")
            return redirect(url_for("nvr.list"))
        except ValueError as e:
            err_nvr: dict | None = webdb.get_nvr(_get_db_path(), nvr_id)
            return render_template(
                "nvr_form.html",
                mode="edit",
                nvr=err_nvr or {},
                error=str(e),
            )
    nvr = webdb.get_nvr(_get_db_path(), nvr_id)
    if not nvr:
        abort(404, f"找不到 NVR id={nvr_id}")
    nvr["password"] = ""
    return render_template("nvr_form.html", mode="edit", nvr=nvr, error=None)


@nvr_bp.route("/<int:nvr_id>/delete", methods=["POST"])
def delete(nvr_id: int):
    """刪除 NVR（連同 cameras；保留 scan_runs/events）。"""
    try:
        result = webdb.delete_nvr(_get_db_path(), nvr_id)
        flash(
            f"已刪除 NVR（同時移除 {result['cameras_deleted']} 台 cameras）",
            "success",
        )
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("nvr.list"))


@nvr_bp.route("/test-connection", methods=["POST"])
def test_connection():
    """AJAX 測試 NVR 連線（不寫入 DB）。

    接受 JSON: {host, port, username, password, verify_ssl}
    回傳 JSON: {ok: bool, message: str, latency_ms: int}
    """
    # Lazy import：避免 module-level import 失敗時整個 Blueprint 被廢
    # （2026-07-09 之前 _HAS_SCANNER=False 會讓後續所有 NVR 路由 500）
    try:
        # 2026-07-09 修：當 clips_app 用 `python web/clips_app.py` 啟動（非 -m），
        # sys.path[0] 會是 C:\cc\NVR\web，這時 `import db` 會被解析成 web/db.py
        # （檔案，不是 package），後續 `from db.sqlite_writer import` 失敗說
        # "db is not a package"。修法：用 importlib 直接指定 db package 的檔案路徑。
        import importlib.util
        import pathlib

        db_pkg_path = pathlib.Path(__file__).resolve().parent.parent / "db"
        if (db_pkg_path / "__init__.py").exists():
            spec = importlib.util.spec_from_file_location(
                "db",
                db_pkg_path / "__init__.py",
                submodule_search_locations=[str(db_pkg_path)],
            )
            if spec and spec.loader:
                db_mod = importlib.util.module_from_spec(spec)
                sys.modules["db"] = db_mod
                spec.loader.exec_module(db_mod)
        from nvr_scanner import AvigilonScanner
    except ImportError as e:
        tb = traceback.format_exc()
        return jsonify(
            {
                "ok": False,
                "message": f"伺服器缺少 nvr_scanner 模組：{e}",
                "traceback": tb[-1500:],
            }
        ), 500

    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "message": "無效的 JSON body"}), 400

    for field in ("host", "port", "username", "password"):
        if not data.get(field):
            return jsonify({"ok": False, "message": f"{field} 必填"}), 400

    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    if not user_nonce or not user_key:
        return jsonify(
            {
                "ok": False,
                "message": "伺服器未設定 AVIGILON_USER_NONCE / AVIGILON_USER_KEY（檢查 .env）",
            }
        ), 500

    nvr_cfg = {
        "host": data["host"],
        "port": int(data["port"]),
        "username": data["username"],
        "password": data["password"],
        "verify_ssl": bool(data.get("verify_ssl", False)),
    }
    scanner = AvigilonScanner(
        nvr_cfg,
        user_nonce=user_nonce,
        user_key=user_key,
        timeout=5,
        verify_ssl=nvr_cfg["verify_ssl"],
    )
    start = time.time()
    try:
        scanner.login()
        latency_ms = int((time.time() - start) * 1000)
        return jsonify(
            {
                "ok": True,
                "message": f"連線成功（latency {latency_ms}ms）",
                "latency_ms": latency_ms,
            }
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return jsonify(
            {
                "ok": False,
                "message": f"連線失敗：{type(e).__name__}: {e}",
                "latency_ms": latency_ms,
            }
        )
    finally:
        try:
            scanner.session.close()
        except Exception:
            pass


@nvr_bp.route("/import", methods=["GET", "POST"])
def import_():
    """CSV / JSON 批次匯入。"""
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("請選擇檔案", "danger")
            return redirect(url_for("nvr.import_"))
        try:
            raw = file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("檔案編碼錯誤（請用 UTF-8）", "danger")
            return redirect(url_for("nvr.import_"))

        fmt = _detect_format(file.filename, raw)
        if fmt == "csv":
            parsed, errors = _parse_csv(raw)
        else:
            parsed, errors = _parse_json(raw)

        if errors:
            return render_template(
                "nvr_import.html",
                errors=errors,
                preview_count=0,
                filename=file.filename,
            )
        if not parsed:
            flash("檔案沒有有效資料", "warning")
            return redirect(url_for("nvr.import_"))

        try:
            result = webdb.bulk_upsert_nvrs(_get_db_path(), parsed)
            flash(
                f"匯入完成：新增 {result['inserted']} 台、更新 {result['updated']} 台"
                f"（共 {result['total']} 筆）",
                "success",
            )
            return redirect(url_for("nvr.list"))
        except Exception as e:
            return render_template(
                "nvr_import.html",
                errors=[f"DB 錯誤（已 rollback）：{e}"],
                preview_count=len(parsed),
                filename=file.filename,
            )

    return render_template(
        "nvr_import.html",
        errors=None,
        preview_count=0,
        filename=None,
    )


@nvr_bp.route("/import/template.csv")
def import_template_csv():
    """下載 CSV 範本（UTF-8 BOM + CRLF）。"""
    example_csv = (
        "id,name,host,port,username,password,verify_ssl,site_id,tags\r\n"
        "ACC8-P4,WIN-OPA34I3TCL5,192.168.133.141,8443,administrator,SECRET,0,,branch;taipei\r\n"
        "BRANCH-B,B 分店,192.168.2.100,8443,api_reader,SECRET,0,,branch;taichung\r\n"
        "HQ-MAIN,總部主 NVR,10.0.0.50,8443,api_reader,SECRET,0,HQ,hq;production\r\n"
    )
    body = "﻿" + example_csv.replace("\r\n", "\n").replace("\n", "\r\n")
    return Response(
        body.encode("utf-8"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nvr_template.csv"'},
    )


@nvr_bp.route("/import/template.json")
def import_template_json():
    """下載 JSON 範本。"""
    example = [
        {
            "id": "ACC8-P4",
            "name": "WIN-OPA34I3TCL5",
            "host": "192.168.133.141",
            "port": 8443,
            "username": "administrator",
            "password": "SECRET",
            "verify_ssl": False,
            "site_id": None,
            "tags": ["branch", "taipei"],
        },
        {
            "id": "BRANCH-B",
            "name": "B 分店",
            "host": "192.168.2.100",
            "port": 8443,
            "username": "api_reader",
            "password": "SECRET",
            "verify_ssl": False,
            "site_id": None,
            "tags": ["branch", "taichung"],
        },
    ]
    return Response(
        json.dumps(example, ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nvr_template.json"'},
    )


@nvr_bp.route("/export.csv")
def export_csv():
    """匯出目前 DB 內所有 NVR 為 CSV。"""
    nvrs = webdb.get_all_nvrs_for_export(_get_db_path())
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_CSV_FIELDS,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writeheader()
    for nvr in nvrs:
        row = dict(nvr)
        row["tags"] = ";".join(row.get("tags") or [])
        row["verify_ssl"] = 1 if row["verify_ssl"] else 0
        if row.get("site_id") is None:
            row["site_id"] = ""
        writer.writerow(row)
    body = "﻿" + buf.getvalue()
    ts = time.strftime("%Y%m%d_%H%M%S")
    return Response(
        body.encode("utf-8"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="nvr_export_{ts}.csv"'},
    )


@nvr_bp.route("/export.json")
def export_json():
    """匯出目前 DB 內所有 NVR 為 JSON array。"""
    nvrs = webdb.get_all_nvrs_for_export(_get_db_path())
    ts = time.strftime("%Y%m%d_%H%M%S")
    return Response(
        json.dumps(nvrs, ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="nvr_export_{ts}.json"'},
    )
