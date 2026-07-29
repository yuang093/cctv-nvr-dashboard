"""
web/app.py
==========
Flask 應用程式（v2 Web UI 雛形）。

啟動：python -m web.app
預設：http://127.0.0.1:5000

環境變數：
    NVR_DB_PATH     SQLite 檔案路徑（預設 ./nvr_scan.db）
    NVR_WEB_HOST    綁定 IP（預設 0.0.0.0；設 127.0.0.1 = 只本機）
    NVR_WEB_PORT    埠號（預設 5000）
    NVR_WEB_DEBUG   True/False（預設 False）
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import traceback
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Module-level logger (server-side, 寫 stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nvr.web")

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request,
    Response, send_file, session as flask_session, url_for,
)

from web import db as webdb
from web import nvr_crud
from web.fleet import get_fleet_view

# AvigilonScanner 從環境變數讀 user_nonce / user_key（透過 nvr_scanner 模組頂層載入）
try:
    from nvr_scanner import AvigilonScanner, load_env_file
    _HAS_SCANNER = True
except ImportError:
    _HAS_SCANNER = False


# === 啟動時 load .env（worker 已經會 load；web 不能漏） ===
# 否則測試連線等需要 AVIGILON_USER_NONCE/KEY 的功能會 fail。
def _bootstrap_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            loaded = load_env_file(env_path)
            if loaded:
                print(f"[INFO] web.app 已從 .env 載入 {loaded} 個環境變數")
        except Exception as e:
            print(f"[WARN] .env 載入失敗：{e}")


_bootstrap_env()


# === Phase 2.5b：CSV / JSON 匯入 helper ===
# 2026-07-13 Phase 3：抽到 web/nvr_crud.py 共用模組
# 從 web.nvr_crud import（重命名為舊 _ 開頭名稱，保持向後相容）
from web.nvr_crud import (
    parse_nvr_form as _parse_nvr_form,
    normalize_nvr_dict as _normalize_nvr_dict,
    parse_csv as _parse_csv,
    parse_json as _parse_json,
    detect_format as _detect_format,
    CSV_FIELDS as _CSV_FIELDS,
)


# === 共用 DB_PATH 取得（給 routes 用）===
def _get_db_path(app: Flask) -> str:
    return app.config["DB_PATH"]


def _start_probe_thread(db_path: str, session_id: int) -> None:
    """Phase 2.8（Arisan）Phase #6：背景啟動 CIDR probe。

    抽成獨立 function 是為了讓測試 monkeypatch（避免 route 啟動的
    thread 與 fixture teardown race）。生產環境行為：用 daemon thread 跑
    run_discovery_for_session，不阻塞 HTTP response。
    """
    import threading
    from web.discover import run_discovery_for_session
    t = threading.Thread(
        target=run_discovery_for_session,
        args=(db_path, session_id),
        daemon=True,
    )
    t.start()


# === NVR form 解析（給 new / edit routes 共用，Phase 2.5a）===
def _parse_nvr_form(form) -> dict:
    """從 request.form 解析 NVR 欄位，做基本驗證。

    Raises:
        ValueError: 欄位缺失或格式錯誤。
    """
    data = {
        "nvr_id": (form.get("nvr_id") or "").strip(),
        "name": (form.get("name") or "").strip(),
        "host": (form.get("host") or "").strip(),
        "port": (form.get("port") or "8443").strip(),
        "username": (form.get("username") or "").strip(),
        "password": form.get("password") or "",  # 不 strip（密碼可能有空白）
        "verify_ssl": form.get("verify_ssl") == "on",
        "site_id": (form.get("site_id") or "").strip(),
        "tags": (form.get("tags") or "").strip(),
    }
    # 驗證
    if not data["nvr_id"]:
        raise ValueError("ID 必填")
    if not data["name"]:
        raise ValueError("名稱必填")
    if not data["host"]:
        raise ValueError("Host 必填")
    if not data["username"]:
        raise ValueError("Username 必填")
    # port 範圍
    try:
        port = int(data["port"])
        if not (1 <= port <= 65535):
            raise ValueError
        data["port"] = port
    except ValueError:
        raise ValueError(f"Port 必須是 1-65535 的數字（收到：{data['port']}）")
    return data


def _probe_one_nvr_online(nvr_cfg: dict, user_nonce: str, user_key: str,
                          timeout: int) -> int:
    """對單台 NVR 連線並回報連線中 cam 數（單獨 try/except，失敗回 0）。"""
    if not _HAS_SCANNER:
        return 0
    try:
        scanner = AvigilonScanner(
            nvr_cfg,
            user_nonce=user_nonce,
            user_key=user_key,
            timeout=timeout,
        )
        scanner.login()
        scanner.get_server_ids()
        cams = scanner.get_cameras()
        return sum(1 for c in cams.values() if c.get("connection_state") == "CONNECTED")
    except Exception as e:
        # 單台失敗不中斷 dashboard；debug 用 logger
        logger.warning("[dashboard online] NVR %s 探測失敗：%s",
                       nvr_cfg.get("name", nvr_cfg.get("host", "?")), e)
        return 0


def _count_online_cameras(db_path: str, *, timeout_per_nvr: int = 5) -> int:
    """Phase 2.8（Arisan 磁磚點擊跳轉）：即時對每台 enabled NVR GET /cameras 算線上數。

    - 並行查（ThreadPoolExecutor，max_workers=4）避免 dashboard hang
    - 缺 AVIGILON_USER_NONCE/KEY 或 NVR 清單為空 → 回 0
    - 單台 NVR 失敗 try/except 吞掉，不影響其他台
    """
    nvrs = webdb.list_enabled_nvrs(db_path)
    if not nvrs:
        return 0
    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    if not user_nonce or not user_key:
        return 0

    total = 0
    with ThreadPoolExecutor(max_workers=min(4, len(nvrs))) as ex:
        futures = [
            ex.submit(_probe_one_nvr_online, n, user_nonce, user_key, timeout_per_nvr)
            for n in nvrs
        ]
        for f in as_completed(futures):
            try:
                total += f.result()
            except Exception:
                pass
    return total


def _register_routes(app: Flask) -> None:
    """把所有 routes 註冊到 app（在 create_app 內呼叫，方便測試換 db_path）。"""
    # 註冊 Jinja filter：{{ e.detected_at | taipei }} 自動轉台灣時間
    app.jinja_env.filters["taipei"] = _to_taipei_str

    @app.context_processor
    def _inject_theme():
        """把 session theme 注入所有 template，讓 base.html 能讀到。"""
        return dict(theme=flask_session.get("theme", ""), dark=flask_session.get("dark", False))

    @app.route("/dark/toggle", methods=["POST"])
    def dark_toggle():
        """切換深色模式。"""
        flask_session["dark"] = not flask_session.get("dark", False)
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/")
    def dashboard():
        stats = webdb.get_overall_stats(_get_db_path(app))
        # Phase 2.8（Arisan 磁磚點擊跳轉）：即時算線上 cam 數（GET 每台 NVR /cameras）。
        stats["online_cameras"] = _count_online_cameras(
            _get_db_path(app), timeout_per_nvr=5
        )
        recent = webdb.get_recent_runs(_get_db_path(app), limit=5)
        # Phase 2.8（Arisan 缺錄排名）：取 24h 缺錄最多的前 5 台 cam
        top_missing = webdb.get_top_missing_cameras(_get_db_path(app), limit=5)
        return render_template(
            "dashboard.html", stats=stats, recent=recent, top_missing=top_missing,
        )

    @app.route("/theme")
    def theme_preview():
        """主題選擇頁面（六種風格預覽）。"""
        current = flask_session.get("theme", "")
        return render_template("theme_preview.html", current=current)

    @app.route("/theme/apply", methods=["POST"])
    def theme_apply():
        """套用選擇的主題（寫入 session）。"""
        theme = request.form.get("theme", "")
        flask_session["theme"] = theme
        flash(f"主題已套用：{theme}", "success")
        return redirect(url_for("dashboard"))

    @app.route("/fleet")
    def fleet():
        """2026-07-29 新功能：跨 NVR 伺服器概覽（總計 / 健康 / 異常 分類）。"""
        nvrs = get_fleet_view(_get_db_path(app))
        total_cams = sum(n["total"] for n in nvrs)
        return render_template(
            "fleet.html",
            nvrs=nvrs,
            total_cams=total_cams,
        )

    @app.route("/runs")
    def runs_list():
        page = _safe_int(request.args.get("page"), 1, min_val=1)
        data = webdb.get_paginated_runs(_get_db_path(app), page=page, per_page=20)
        return render_template("runs_list.html", **data)

    @app.route("/runs/<int:run_id>")
    def run_detail(run_id: int):
        run = webdb.get_run(_get_db_path(app), run_id)
        if not run:
            abort(404, f"找不到 scan_run_id={run_id}")
        events = webdb.get_run_events(_get_db_path(app), run_id)
        cameras = webdb.get_run_cameras(_get_db_path(app), run_id)
        # 個別 NVR 連線失敗清單（v2.7+ 起）
        nvr_failures = webdb.get_nvr_failures_for_run(_get_db_path(app), run_id)
        # 從這次 run 的 events 聚合故障相機（給「故障相機彙總」section 用）
        grouped = _group_run_events_by_camera(events)
        return render_template(
            "run_detail.html", run=run, events=events, cameras=cameras,
            grouped=grouped, topic_zh=webdb.get_topic_zh,
            nvr_failures=nvr_failures,
        )

    @app.route("/nvrs")
    def nvrs_list():
        page = _safe_int(request.args.get("page"), 1, min_val=1)
        q = (request.args.get("q") or "").strip() or None
        data = webdb.get_nvrs_paginated(
            _get_db_path(app), page=page, per_page=20, q=q,
        )
        return render_template(
            "nvrs_list.html",
            nvrs=data["nvrs"],
            total=data["total"],
            page=data["page"],
            per_page=data["per_page"],
            total_pages=data["total_pages"],
            q=q or "",
        )

    @app.route("/nvrs/<int:internal_id>/toggle", methods=["POST"])
    def nvr_toggle_enabled(internal_id: int):
        """Phase 2.7+：切換單台 NVR 的啟用狀態（不刪除資料）。

        啟用 = 1 會被背景掃描；啟用 = 0 跳過。
        從哪裡來就回哪裡（支援從 NVR 清單頁觸發）。
        """
        nvr = webdb.get_nvr(_get_db_path(app), internal_id)
        if nvr is None:
            abort(404, f"找不到 internal_id={internal_id}")
        new_enabled = not bool(nvr.get("enabled", 1))
        webdb.set_nvr_enabled(_get_db_path(app), internal_id, new_enabled)
        flash(
            f"NVR「{nvr['name']}」已{'啟用' if new_enabled else '停用'}",
            "success" if new_enabled else "warning",
        )
        # 從哪裡來就回哪裡（referer）；fallback 為 /nvrs
        return redirect(request.referrer or url_for("nvrs_list"))

    @app.route("/nvrs/new", methods=["GET", "POST"])
    def nvr_new():
        """Phase 2.5a：新增 NVR。"""
        if request.method == "POST":
            try:
                nvr_data = _parse_nvr_form(request.form)
                nvr_data["password"] = request.form.get("password") or ""
                if not nvr_data["password"]:
                    raise ValueError("密碼必填（新增時）")
                new_id = webdb.create_nvr(_get_db_path(app), nvr_data)
                flash(f"已建立 NVR「{nvr_data['nvr_id']}」", "success")
                return redirect(url_for("nvrs_list"))
            except ValueError as e:
                # 保留使用者輸入以便修正
                return render_template(
                    "nvr_form.html", mode="new", nvr=request.form.to_dict(),
                    error=str(e),
                )
        return render_template("nvr_form.html", mode="new", nvr={}, error=None)

    @app.route("/nvrs/<int:nvr_id>/edit", methods=["GET", "POST"])
    def nvr_edit(nvr_id):
        """Phase 2.5a：編輯 NVR。密碼留空=不變更。"""
        if request.method == "POST":
            try:
                nvr_data = _parse_nvr_form(request.form)
                password = request.form.get("password") or ""
                password_changed = bool(password)
                if password_changed:
                    nvr_data["password"] = password
                webdb.update_nvr(
                    _get_db_path(app), nvr_id, nvr_data,
                    password_changed=password_changed,
                )
                flash(f"已更新 NVR「{nvr_data['nvr_id']}」", "success")
                return redirect(url_for("nvrs_list"))
            except ValueError as e:
                nvr = webdb.get_nvr(_get_db_path(app), nvr_id) or {}
                return render_template(
                    "nvr_form.html", mode="edit", nvr=nvr, error=str(e),
                )
        nvr = webdb.get_nvr(_get_db_path(app), nvr_id)
        if not nvr:
            abort(404, f"找不到 NVR id={nvr_id}")
        # 清空密碼欄位（永遠不在 UI 顯示舊密碼）
        nvr["password"] = ""
        return render_template("nvr_form.html", mode="edit", nvr=nvr, error=None)

    @app.route("/nvrs/<int:nvr_id>/delete", methods=["POST"])
    def nvr_delete(nvr_id):
        """Phase 2.5a：刪除 NVR（連同 cameras；保留 scan_runs/events）。"""
        try:
            result = webdb.delete_nvr(_get_db_path(app), nvr_id)
            flash(
                f"已刪除 NVR（同時移除 {result['cameras_deleted']} 台 cameras）",
                "success",
            )
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("nvrs_list"))

    @app.route("/nvrs/test-connection", methods=["POST"])
    def nvr_test_connection():
        """Phase 2.5a+：AJAX 測試 NVR 連線（不寫入 DB）。

        接受 JSON: {host, port, username, password, verify_ssl}
        回傳 JSON: {ok: bool, message: str, latency_ms: int}

        需要 .env 已設定 AVIGILON_USER_NONCE / AVIGILON_USER_KEY
        （worker 也用同一組；NVR API 認證強制要求）。
        """
        if not _HAS_SCANNER:
            return jsonify({
                "ok": False,
                "message": "伺服器缺少 nvr_scanner 模組（pip install -r requirements.txt）",
            }), 500

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
            return jsonify({
                "ok": False,
                "message": "伺服器未設定 AVIGILON_USER_NONCE / AVIGILON_USER_KEY（檢查 .env）",
            }), 500

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
            timeout=5,  # 測試用，縮短 timeout
            verify_ssl=nvr_cfg["verify_ssl"],
        )
        start = time.time()
        try:
            scanner.login()
            latency_ms = int((time.time() - start) * 1000)
            return jsonify({
                "ok": True,
                "message": f"連線成功（latency {latency_ms}ms）",
                "latency_ms": latency_ms,
            })
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            return jsonify({
                "ok": False,
                "message": f"連線失敗：{type(e).__name__}: {e}",
                "latency_ms": latency_ms,
            })
        finally:
            try:
                scanner.session.close()
            except Exception:
                pass

    @app.route("/nvrs/import", methods=["GET", "POST"])
    def nvr_import():
        """Phase 2.5b：CSV / JSON 批次匯入（80+ NVR 用）。"""
        if request.method == "POST":
            file = request.files.get("file")
            if not file or not file.filename:
                flash("請選擇檔案", "danger")
                return redirect(url_for("nvr_import"))
            try:
                raw = file.read().decode("utf-8-sig")  # 容忍 BOM
            except UnicodeDecodeError:
                flash("檔案編碼錯誤（請用 UTF-8）", "danger")
                return redirect(url_for("nvr_import"))

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
                return redirect(url_for("nvr_import"))

            # 整批寫入（all-or-nothing transaction）
            # Phase 2.5c 起改用 upsert：id 已存在 → 更新；不存在 → 新增
            # （支援 round-trip workflow：匯出 → 修改 → 匯入）
            try:
                result = webdb.bulk_upsert_nvrs(
                    _get_db_path(app), parsed,
                )
                flash(
                    f"匯入完成：新增 {result['inserted']} 台、更新 {result['updated']} 台"
                    f"（共 {result['total']} 筆）",
                    "success",
                )
                return redirect(url_for("nvrs_list"))
            except Exception as e:
                logger.exception("bulk import failed: %s", e)
                return render_template(
                    "nvr_import.html",
                    errors=[f"DB 錯誤（已 rollback）：{e}"],
                    preview_count=len(parsed),
                    filename=file.filename,
                )

        return render_template(
            "nvr_import.html",
            errors=None, preview_count=0, filename=None,
        )

    @app.route("/nvrs/import/template.csv")
    def nvr_import_template_csv():
        """下載 CSV 範本（含 UTF-8 BOM + CRLF 換行）。

        Excel 雙擊 CSV 時傾向用系統 codepage（cp950）解碼 → 中文亂碼。
        兩個措施提高 Excel 相容性：
        1. UTF-8 BOM (﻿) — Excel 365 通常會認得
        2. CRLF 換行 — Windows 風格，Excel 更友善
        若仍亂碼：用「資料 → 從文字檔 → 編碼選 UTF-8」匯入精靈。
        """
        example_csv = (
            "id,name,host,port,username,password,verify_ssl,site_id,tags\r\n"
            "ACC8-P4,WIN-OPA34I3TCL5,192.168.133.141,8443,administrator,SECRET,0,,branch;taipei\r\n"
            "BRANCH-B,B 分店,192.168.2.100,8443,api_reader,SECRET,0,,branch;taichung\r\n"
            "HQ-MAIN,總部主 NVR,10.0.0.50,8443,api_reader,SECRET,0,HQ,hq;production\r\n"
        )
        # 加 UTF-8 BOM + 確保 CRLF
        body = "﻿" + example_csv.replace("\r\n", "\n").replace("\n", "\r\n")
        return Response(
            body.encode("utf-8"),
            mimetype="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="nvr_template.csv"',
            },
        )

    @app.route("/nvrs/import/template.json")
    def nvr_import_template_json():
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
            headers={
                "Content-Disposition": 'attachment; filename="nvr_template.json"',
            },
        )

    # === Phase 2.5c：匯出目前 NVR 清單（round-trip 匯出 → 修改 → 匯入） ===

    @app.route("/nvrs/export.csv")
    def nvr_export_csv():
        """匯出目前 DB 內所有 NVR 為 CSV。

        格式與 `nvr_import_template_csv` 對稱，可直接編輯後再匯入。
        含 UTF-8 BOM + CRLF（Excel 相容性）。
        filename 含時間戳：`nvr_export_YYYYMMDD_HHMMSS.csv`
        """
        nvrs = webdb.get_all_nvrs_for_export(_get_db_path(app))
        # 用 csv 模組產生內容（處理 quote 跳脫）
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=_CSV_FIELDS,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\r\n",
        )
        writer.writeheader()
        for nvr in nvrs:
            # tags: list → "a;b;c"
            row = dict(nvr)
            row["tags"] = ";".join(row.get("tags") or [])
            # verify_ssl: bool → 0/1（跟範本一致）
            row["verify_ssl"] = 1 if row["verify_ssl"] else 0
            # site_id None → 空字串（CSV 友善）
            if row.get("site_id") is None:
                row["site_id"] = ""
            writer.writerow(row)
        body = "﻿" + buf.getvalue()  # 加 UTF-8 BOM
        ts = time.strftime("%Y%m%d_%H%M%S")
        return Response(
            body.encode("utf-8"),
            mimetype="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="nvr_export_{ts}.csv"'
                ),
            },
        )

    @app.route("/nvrs/export.json")
    def nvr_export_json():
        """匯出目前 DB 內所有 NVR 為 JSON array。

        格式與 `nvr_import_template_json` 對稱。
        filename 含時間戳：`nvr_export_YYYYMMDD_HHMMSS.json`
        """
        nvrs = webdb.get_all_nvrs_for_export(_get_db_path(app))
        ts = time.strftime("%Y%m%d_%H%M%S")
        return Response(
            json.dumps(nvrs, ensure_ascii=False, indent=2),
            mimetype="application/json; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="nvr_export_{ts}.json"'
                ),
            },
        )

    # === Phase 2.6：背景掃描 + 故障總覽 ===

    @app.route("/scan", methods=["POST"])
    def scan_trigger():
        """啟動背景 thread 跑 batch_scan。

        若已在跑，回 409 conflict。
        同步回 202 + scan 起始資訊；前端輪詢 /scan/status 看進度。
        """
        with _scan_lock:
            if _scan_state["running"]:
                return jsonify({
                    "ok": False,
                    "error": "已有掃描在進行中",
                    "started_at": _scan_state["started_at"],
                }), 409

            db_path = _get_db_path(app)
            # 2026-07-13 Phase 2.3：DB-level lock 檢查（避免外部 cron 同時跑）
            from db.sqlite_writer import acquire_scan_lock
            if not acquire_scan_lock(db_path, timeout=0):
                logger.warning("scan_trigger: DB 內已有 status='running' 的 scan_run")
                return jsonify({
                    "ok": False,
                    "error": "已有掃描在進行中（DB 內 status='running'，可能是 cron 或其他 process）",
                }), 409

            # 2026-07-13 Phase 2.1：DB-as-source-of-truth，從 DB 撈 enabled NVR
            try:
                enabled_nvrs = webdb.list_enabled_nvrs(db_path)
                enabled_count = len(enabled_nvrs)
            except Exception as e:
                logger.warning("scan_trigger: 從 DB 撈 enabled NVR 失敗: %s", e)
                enabled_count = 0
            _reset_scan_state(total_nvrs=enabled_count)

            # 啟動 background thread（仍在 lock 內避免 TOCTOU）
            thread = threading.Thread(
                target=_run_scan_in_background,
                args=(app, db_path),
                daemon=True,
                name="nvr-scan",
            )
            thread.start()

        return jsonify({
            "ok": True,
            "started_at": _scan_state["started_at"],
            "total_nvrs": enabled_count,
        }), 202

    @app.route("/scan/status")
    def scan_status():
        """查目前 scan 狀態（給前端 polling）。"""
        with _scan_lock:
            return jsonify(dict(_scan_state))

    @app.route("/abnormal")
    def abnormal_list():
        """故障攝影機總覽（按相機分組）。"""
        groups = webdb.get_abnormal_cameras_grouped(_get_db_path(app))
        # 統計
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
            last_run=webdb.get_last_scan_run(_get_db_path(app)),
        )

    @app.route("/abnormal/export.pdf")
    def abnormal_export_pdf():
        """故障報告 PDF（繁體中文，reportlab 內嵌字型）。

        版面：
            - 標題 + 生成時間
            - 總覽：受影響 NVR 數 / 相機數 / 總事件數
            - 每台 NVR 一段：相機清單（名稱 + 故障類型中文 + 首次/最新）

        注意：此端點每次都即時生成「最新」PDF，不存檔。
              歷史歸檔請改用 /reports 列表 → 點選下載。
        """
        groups = webdb.get_abnormal_cameras_grouped(_get_db_path(app))
        topic_zh = webdb.get_topic_zh
        last_run = webdb.get_last_scan_run(_get_db_path(app))

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

    @app.route("/reports")
    def reports_list():
        """歷史 PDF 報告列表（每次掃描自動歸檔一份）。"""
        from web import report_archive
        db_path = _get_db_path(app)
        items = report_archive.list_reports(db_path)
        return render_template("reports_list.html", items=items)

    @app.route("/reports/download/<int:run_id>")
    def reports_download(run_id: int):
        """下載指定 run_id 的歷史 PDF。"""
        from web import report_archive
        db_path = _get_db_path(app)
        fpath = report_archive.find_report(db_path, run_id)
        if fpath is None or not fpath.exists():
            abort(404, description=f"找不到 run_id={run_id} 的歸檔報告")
        # send_file 會自動處理 mime + Content-Disposition
        return send_file(
            fpath,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=fpath.name,
        )

    @app.route("/events")
    def events_list():
        hours = _safe_int(request.args.get("hours"), 24, min_val=1, max_val=8760)
        nvr_id = request.args.get("nvr_id", type=int)
        topic = request.args.get("topic") or None
        status = request.args.get("status", "all")
        # 防止無效值 fallback 到 all
        if status not in ("all", "open", "resolved"):
            status = "all"
        events = webdb.get_events_filtered(
            _get_db_path(app),
            hours=hours, nvr_id=nvr_id, topic=topic,
            status=status, limit=200,
        )
        all_topics = webdb.get_all_topics(_get_db_path(app))
        all_nvrs = webdb.get_nvrs(_get_db_path(app))
        return render_template(
            "events_list.html",
            events=events, hours=hours, nvr_id=nvr_id,
            topic=topic or "", status=status,
            all_topics=all_topics, all_nvrs=all_nvrs,
            topic_zh=webdb.get_topic_zh,
        )

    @app.route("/query", methods=["GET", "POST"])
    def adhoc_query():
        """Phase 1 Step 3b：ad-hoc 唯讀 SELECT 頁。

        GET：顯示表單 + 範例 SQL
        POST：執行並顯示結果
        """
        result = None
        error = None
        sql = ""
        # 預設範例
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
                    result = webdb.run_readonly_query(
                        _get_db_path(app), sql, max_rows=500,
                    )
                except ValueError as exc:
                    error = str(exc)
        return render_template(
            "query.html",
            sql=sql, example_sql=example_sql,
            result=result, error=error,
        )

    @app.route("/wall")
    def wall():
        """2026-07-29 重構：相機牆視覺化 grid（含縮圖 + status 圓點 + 計數 tab）。

        ?filter=all|online|signal_lost|no_signal（預設 all）
        """
        filter_kind = request.args.get("filter", "all")
        if filter_kind not in ("all", "online", "signal_lost", "no_signal"):
            filter_kind = "all"
        cams = webdb.get_wall_cameras_with_snapshots(_get_db_path(app), filter_kind=filter_kind)
        counts = webdb.get_wall_filter_counts(_get_db_path(app))
        return render_template(
            "wall.html",
            cams=cams, filter_kind=filter_kind, counts=counts,
        )

    @app.route("/devices")
    def devices_list():
        """Phase 2.8（Arisan）Phase #5：跨 NVR 設備總覽表。"""
        nvr_filter = request.args.get("nvr", "").strip()
        status_filter = request.args.get("status", "").strip()
        page = max(1, int(request.args.get("page", "1") or "1"))
        per_page = 50
        rows, total = webdb.get_devices_paginated(
            _get_db_path(app), page=page, per_page=per_page,
            nvr_filter=nvr_filter, status_filter=status_filter,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        nvrs = webdb.get_nvrs(_get_db_path(app))
        return render_template(
            "devices_list.html",
            rows=rows, total=total, page=page, per_page=per_page,
            total_pages=total_pages,
            nvr_filter=nvr_filter, status_filter=status_filter, nvrs=nvrs,
        )

    @app.route("/devices/discover", methods=["GET", "POST"])
    def devices_discover():
        """Phase 2.8（Arisan）Phase #5 介面 + Phase #6 探索網段執行。"""
        if request.method == "POST":
            cidr = (request.form.get("cidr") or "").strip()
            port = int(request.form.get("port") or "8443")
            if not cidr:
                return render_template(
                    "discover.html",
                    error="請輸入 CIDR（例如 192.168.0.0/24）",
                    cidr=cidr, port=port,
                ), 400
            # 先 validate CIDR 格式（fail fast）
            from web.discover import expand_cidr
            try:
                expand_cidr(cidr)
            except ValueError as e:
                return render_template(
                    "discover.html",
                    error=str(e), cidr=cidr, port=port,
                ), 400
            # 建 session（status='pending'）
            try:
                session_id = webdb.create_discover_session(
                    _get_db_path(app), cidr=cidr, port=port,
                )
            except Exception as e:
                return render_template(
                    "discover.html",
                    error=str(e), cidr=cidr, port=port,
                ), 400
            # Phase #6：背景執行 CIDR probe（避免 /24 等 96s 阻塞 HTTP）
            # 抽成 _start_probe_thread helper，方便測試 monkeypatch 掉背景 thread
            _start_probe_thread(_get_db_path(app), session_id)
            return redirect(url_for("devices_discover_result", session_id=session_id))
        return render_template("discover.html", cidr="192.168.0.0/24", port=8443)

    @app.route("/devices/discover/<int:session_id>")
    def devices_discover_result(session_id: int):
        """Phase #5 補：顯示單次探索 session 結果（Phase #6 探索邏輯未做，先回空殼頁）。"""
        sess = webdb.get_discover_session(_get_db_path(app), session_id)
        if not sess:
            abort(404)
        return render_template("discover_result.html", session=sess)

    @app.route("/devices/<device_id>")
    def device_detail(device_id: str):
        """Phase 2.8（Arisan）Phase #5：單台 cam 詳情 + 影像健康卡。"""
        info = webdb.get_device_detail(_get_db_path(app), device_id)
        if not info:
            abort(404)
        return render_template("device_detail.html", cam=info)

    @app.route("/health/cameras/<device_id>")
    def camera_health_history(device_id: str):
        """Phase 2.8（Arisan）Phase #5：單台 cam 健康歷史（image_health_checks）。"""
        info = webdb.get_device_detail(_get_db_path(app), device_id)
        if not info:
            abort(404)
        history = webdb.get_camera_health_history(_get_db_path(app), device_id, limit=50)
        return render_template(
            "camera_health.html",
            cam=info, history=history,
        )

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message=str(e)), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500, message=str(e)), 500


def _make_flask_app() -> Flask:
    """建 Flask app，自動處理 PyInstaller 內 templates / static 路徑。

    一般開發：`templates` / `static` 跟 web/app.py 同目錄。
    PyInstaller one-dir：`web/templates` 被抽到 `_internal/web/templates/`，
    需要從 `sys._MEIPASS`（runtime root）絕對路徑重新指。
    """
    import sys
    if getattr(sys, "frozen", False):
        # 打包狀態（onedir）：sys._MEIPASS = dist/web/_internal（templates在 _internal/web/templates）
        meipass = Path(sys._MEIPASS)
        tpl_dir = meipass / "web" / "templates"
        static_dir = meipass / "web" / "static"
        app = Flask(
            __name__,
            template_folder=str(tpl_dir),
            static_folder=str(static_dir),
        )
    else:
        # 開發模式：Flask 預設找 web/templates / web/static（跟 .py 同目錄）
        app = Flask(__name__)
    return app


def create_app(db_path: str | None = None) -> Flask:
    """
    Flask app factory。

    Args:
        db_path: SQLite 路徑。None 時從 NVR_DB_PATH env var 或預設 ./nvr_scan.db 讀。
    """
    app = _make_flask_app()
    app.config["DB_PATH"] = db_path or os.environ.get(
        "NVR_DB_PATH", "./nvr_scan.db"
    )
    # flash() 需要 SECRET_KEY；v2 雛形階段用固定字串足夠
    # （正式部署應從 env var 注入；不在 v2 範圍）
    app.config["SECRET_KEY"] = os.environ.get(
        "NVR_WEB_SECRET_KEY", "nvr-scanner-dev-key-change-in-prod"
    )
    # 確認 DB 存在（避免啟動後第一個 request 才 500）
    if app.config["DB_PATH"] != ":memory:" and not Path(
        app.config["DB_PATH"]
    ).exists():
        print(
            f"[WARN] DB 檔不存在：{app.config['DB_PATH']}"
            "（網頁將顯示空資料；先跑 python nvr_scanner.py 建立）"
        )
    # Phase 2.8（Arisan）：Web UI 啟動時主動跑 schema migration，
    # 避免用戶只開網頁沒跑 worker 時，新表（如 discover_sessions）缺失導致 500。
    if app.config["DB_PATH"] != ":memory:":
        try:
            from db.sqlite_writer import SqliteWriter
            SqliteWriter(app.config["DB_PATH"]).close()
        except Exception as e:
            print(f"[WARN] schema init 失敗：{e}")
    _register_routes(app)
    return app


def _group_run_events_by_camera(events: list[dict]) -> list[dict]:
    """把 /runs/<id> 詳情頁的 events 列表 group by (nvr_id, device_id)。

    給 run_detail.html 的「故障相機彙總」section 用。
    """
    groups: dict[tuple, dict] = {}
    for e in events:
        key = (e["nvr_id"], e["device_id"])
        if key not in groups:
            groups[key] = {
                "nvr_id": e["nvr_id"],
                "nvr_name": e.get("nvr_name") or "?",
                "device_id": e["device_id"],
                "camera_name": e.get("camera_name") or f"(unknown #{e['device_id']})",
                "topics": [],
                "first_detected": e.get("occurred_at") or e.get("detected_at"),
                "last_detected":  e.get("occurred_at") or e.get("detected_at"),
                "open_count": 0,
            }
        g = groups[key]
        if e["event_topic"] not in g["topics"]:
            g["topics"].append(e["event_topic"])
        g["open_count"] += 1
        if e.get("occurred_at") and (
            not g["first_detected"] or e["occurred_at"] < g["first_detected"]
        ):
            g["first_detected"] = e["occurred_at"]
        if e.get("occurred_at") and (
            not g["last_detected"] or e["occurred_at"] > g["last_detected"]
        ):
            g["last_detected"] = e["occurred_at"]
    return sorted(
        groups.values(),
        key=lambda g: (g["nvr_name"], g["first_detected"] or ""),
    )


# === Query string 安全轉 int ===
def _safe_int(value: str | None, default: int, *, min_val: int = 0, max_val: int = 2**31) -> int:
    """把 query string 轉 int，無效時退回 default。

    防止 `?page=abc` 噴 ValueError → 500。
    """
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (ValueError, TypeError):
        return default
    if n < min_val or n > max_val:
        return default
    return n


# === 時區：DB 存 UTC，UI / PDF 顯示為 Asia/Taipei ===
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _to_taipei_str(iso_utc: str | None) -> str:
    """把 ISO 8601 UTC 字串轉成 Asia/Taipei（顯示用）。

    輸入：'2026-07-03T05:39:02Z' 或 '2026-07-03T05:39:02+00:00'
    輸出：'2026-07-03 13:39:02'
    None / 空字串 → 原文回傳。
    """
    if not iso_utc:
        return iso_utc or ""
    try:
        s = iso_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_utc


def _build_abnormal_pdf(
    groups: list[dict],
    topic_zh: dict[str, str],
    last_run: dict | None,
) -> bytes:
    """產生故障報告 PDF（繁體中文）。

    版面（reportlab platypus，優化版）：
        - 標題列（深藍底白字）
        - 總覽段落：受影響 NVR/相機/事件數 + 最後一次掃描時間
        - 依 NVR 分段，每段一個 Table（中文 row 資料）
        - 頁尾：頁碼 + 生成時間
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    # 1. 註冊中文字型（Windows 內建微軟正黑體）
    font_name = "MicrosoftJhengHei"
    font_bold = "MicrosoftJhengHei-Bold"
    try:
        pdfmetrics.registerFont(TTFont(font_name, r"C:\Windows\Fonts\msjh.ttc"))
        pdfmetrics.registerFont(TTFont(font_bold, r"C:\Windows\Fonts\msjhbd.ttc"))
        logger.debug("PDF 使用字型：Microsoft JhengHei")
    except Exception as e1:
        logger.warning("PDF 字型 msjh.ttc 載入失敗: %s，嘗試 kaiu.ttf", e1)
        # fallback 到標楷體
        try:
            pdfmetrics.registerFont(TTFont(font_name, r"C:\Windows\Fonts\kaiu.ttf"))
            font_bold = font_name
            logger.info("PDF 使用字型：標楷體（fallback）")
        except Exception as e2:
            # 最後 fallback（中文會變方框，但不會 crash）
            font_name = "Helvetica"
            font_bold = "Helvetica-Bold"
            logger.warning("PDF 字型 kaiu.ttf 也無法載入: %s，中文將以方框顯示", e2)

    # 2. 樣式
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"],
        fontName=font_bold, fontSize=20,
        textColor=colors.HexColor("#1a365d"),
        spaceAfter=8, alignment=0,  # left
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"],
        fontName=font_name, fontSize=9,
        textColor=colors.HexColor("#666666"),
        spaceAfter=4,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontName=font_bold, fontSize=13,
        textColor=colors.HexColor("#2c5282"),
        spaceBefore=10, spaceAfter=4,
        borderPadding=4,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontName=font_name, fontSize=9, leading=12,
    )

    # 3. 文件
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="NVR 異常攝影機報告",
    )
    story = []

    # 標題
    gen_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    story.append(Paragraph("NVR 異常攝影機報告", title_style))
    story.append(Paragraph(f"產生時間：{gen_at}", meta_style))

    # 總覽
    total_cameras = len(groups)
    total_open = sum(g["open_count"] for g in groups)
    affected_nvrs = len({g["nvr_id"] for g in groups})
    if last_run:
        run_info = (
            f"最後一次掃描：{_to_taipei_str(last_run.get('started_at')) or '—'}"
            f"（status: {last_run.get('status', '—')}，"
            f"異常相機: {last_run.get('abnormal_cameras', 0)}）"
        )
    else:
        run_info = "最後一次掃描：尚無資料"
    overview_data = [
        ["受影響 NVR 數", f"{affected_nvrs} 台"],
        ["受影響攝影機數", f"{total_cameras} 台"],
        ["未解決事件總數", f"{total_open} 筆"],
    ]
    overview_table = Table(
        overview_data, colWidths=[60 * mm, 80 * mm],
    )
    overview_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#2c5282")),
        ("FONTNAME", (0, 0), (0, -1), font_bold),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#f7fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(overview_table)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(run_info, meta_style))

    # 依 NVR 分組
    if not groups:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(
            "✓ 目前沒有任何未解決的異常事件。",
            body_style,
        ))
    else:
        # 依 NVR 聚合
        nvr_buckets: dict[int, list[dict]] = {}
        for g in groups:
            nvr_buckets.setdefault(g["nvr_id"], []).append(g)

        for nvr_id in nvr_buckets:
            cams = nvr_buckets[nvr_id]
            nvr_name = cams[0]["nvr_name"]
            nvr_total = sum(c["open_count"] for c in cams)
            story.append(Paragraph(
                f"📡 {nvr_name}（{nvr_total} 筆未解決）",
                section_style,
            ))
            # Table：相機名稱 / 故障類型 / 首次 / 最新
            data = [["攝影機", "故障類型", "首次發現", "最新一次"]]
            for c in cams:
                topics_zh = "、".join(
                    topic_zh(t) for t in c["topics"]
                )
                data.append([
                    c["camera_name"],
                    Paragraph(topics_zh, body_style),
                    _to_taipei_str(c["first_detected"]),
                    _to_taipei_str(c["last_detected"]),
                ])
            tbl = Table(
                data, colWidths=[50 * mm, 60 * mm, 30 * mm, 30 * mm],
                repeatRows=1,
            )
            tbl.setStyle(TableStyle([
                # header
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c5282")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), font_bold),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                # body
                ("FONTNAME", (0, 1), (-1, -1), font_name),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                # grid
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#a0aec0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                # padding
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                # alternating row bg
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#f7fafc")]),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 4 * mm))

    # 頁尾
    def _on_page(canv, doc_):
        canv.saveState()
        canv.setFont(font_name, 8)
        canv.setFillColor(colors.HexColor("#999999"))
        canv.drawString(
            18 * mm, 10 * mm,
            f"NVR Scanner · {gen_at}",
        )
        canv.drawRightString(
            A4[0] - 18 * mm, 10 * mm,
            f"第 {doc_.page} 頁",
        )
        canv.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()


# === PDF 報告自動歸檔（batch_scan 完成後觸發） ===

def _archive_current_report(db_path: str, last_run: dict) -> None:
    """產生 PDF 並存到 ./reports/。

    給 _run_scan_in_background 呼叫；任何例外往外拋但不中斷 scan 流程。
    """
    from web import report_archive

    groups = webdb.get_abnormal_cameras_grouped(db_path)
    pdf_bytes = _build_abnormal_pdf(groups, webdb.get_topic_zh, last_run)
    report_archive.save_report(db_path, last_run["id"], pdf_bytes)


# === Scan state（process 級別全域；多 worker 不共享） ===
# Phase 2.6：background thread 跑 batch_scan；UI 透過 /scan/status 查進度。
_scan_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "run_id": None,
    "total_nvrs": 0,
    "ok_nvrs": 0,
    "failed_nvrs": 0,
    "abnormal_cameras": 0,
    "error": None,
}
_scan_lock = threading.RLock()   # RLock 才能在 scan_trigger「握著鎖呼叫 _reset_scan_state」時不死鎖（2026-07-07）


def _reset_scan_state(total_nvrs: int | None = None) -> None:
    """重置 scan 狀態。total_nvrs=None 時保留現值（2026-07-13 Phase 2.2）。"""
    with _scan_lock:
        _scan_state.update({
            "running": True,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "finished_at": None,
            "run_id": None,
            "ok_nvrs": 0,
            "failed_nvrs": 0,
            "abnormal_cameras": 0,
            "error": None,
        })
        if total_nvrs is not None:
            _scan_state["total_nvrs"] = total_nvrs


def _finish_scan_state(success: bool, **kwargs) -> None:
    with _scan_lock:
        _scan_state["running"] = False
        _scan_state["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
        )
        _scan_state.update(kwargs)
        if not success and "error" not in kwargs:
            _scan_state["error"] = "scan failed"


def _run_scan_in_background(app: Flask, db_path: str) -> None:
    """背景 thread 跑 batch_scan（用 SqliteWriter 自己建連線，安全）。

    Phase 2.7+ 起：從 DB nvr_servers 表讀啟用的 NVR（取代 nvr_config.json）。
    """
    try:
        from batch_scan import batch_scan
        from nvr_scanner import get_credential
        from db.sqlite_writer import SqliteWriter

        # 1. 從 DB 讀啟用的 NVR（單一 source of truth）
        enabled = webdb.list_enabled_nvrs(db_path)
        if not enabled:
            _finish_scan_state(
                success=False, error="DB 沒有啟用的 NVR（請到 /nvrs 新增並啟用）",
            )
            return
        cfg = {"nvr_servers": enabled, "scan_settings": {"timeout_seconds": 10}}
        # 2026-07-13 Phase 2.2：不 reset total_nvrs（保留 scan_trigger 已 set 的值）
        _reset_scan_state()

        # 2. 認證
        user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
        user_key = os.environ.get("AVIGILON_USER_KEY", "")
        integration_id = os.environ.get("AVIGILON_INTEGRATION_ID", "")
        username_override = os.environ.get("AVIGILON_USERNAME") or None
        password_override = os.environ.get("AVIGILON_PASSWORD") or None
        if not user_nonce or not user_key:
            _finish_scan_state(
                success=False,
                error="伺服器未設定 AVIGILON_USER_NONCE/KEY（檢查 .env）",
            )
            return
        credentials = {
            "user_nonce": user_nonce,
            "user_key": user_key,
            "integration_id": integration_id,
            "username_override": username_override,
            "password_override": password_override,
        }

        # 3. 跑 batch_scan（verbose=False 避免 thread 印一堆）
        #    用 with 確保 connection 一定關閉（避免 Windows file handle 殘留）
        with SqliteWriter(db_path) as writer:
            result = batch_scan(
                cfg, credentials, writer,
                timeout=cfg["scan_settings"].get("timeout_seconds", 10),
                verbose=False,
            )
        # 4. 寫入最終狀態
        last_run = webdb.get_last_scan_run(db_path)
        # 4a. 自動歸檔這次掃描的 PDF 報告（失敗不影響 scan 狀態）
        if last_run:
            try:
                _archive_current_report(db_path, last_run)
            except Exception as arch_exc:
                logger.warning("PDF 歸檔失敗（不影響 scan 結果）: %s", arch_exc)
        _finish_scan_state(
            success=True,
            run_id=last_run["id"] if last_run else None,
            ok_nvrs=result.get("ok_nvrs", 0),
            failed_nvrs=result.get("failed_nvrs", 0),
            abnormal_cameras=result.get("abnormal_cameras", 0),
            error=None,
        )
    except Exception as exc:
        logger.exception("background scan failed: %s", exc)
        _finish_scan_state(
            success=False, error=f"{type(exc).__name__}: {exc}",
        )


# === 預設 app（給 flask run / python -m web.app 用）===
app = create_app()


# === CLI 入口 ===
def _print_banner(host: str, port: int) -> None:
    """印 banner：本機 + 所有 LAN IP 的 URL，方便員工看。

    0.0.0.0 對員工沒意義，要列 127.0.0.1 + 實際 NIC IP。
    """
    print("=" * 64)
    print(f"[INFO] NVR Web UI 已啟動")
    print(f"[INFO] 綁定：{host}:{port}")
    print(f"[INFO] 資料庫：{app.config['DB_PATH']}")
    print()
    print("  本機存取：")
    print(f"    http://127.0.0.1:{port}/")
    print()
    print("  LAN 內其他電腦存取（任一皆可）：")
    if host in ("0.0.0.0", ""):
        import socket as _s
        for ip in _collect_lan_ips():
            print(f"    http://{ip}:{port}/")
    elif host == "127.0.0.1":
        print("    （目前只綁本機，LAN 端連不到）")
    else:
        print(f"    http://{host}:{port}/")
    print()
    print("  按 Ctrl+C 停止伺服器")
    print("=" * 64)


def _collect_lan_ips() -> list[str]:
    """列出本機所有非 loopback 的 IPv4（給 0.0.0.0 bind 時參考用）。"""
    import socket
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # fallback：用連線 trick 把本機所有 NIC 找出來
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                ips.append(ip)
        except Exception:
            pass
    return ips


def _maybe_open_browser(host: str, port: int, auto_open: bool) -> None:
    """啟動後自動開瀏覽器（背景 thread，不阻塞 server）。"""
    if not auto_open:
        return
    import threading
    import time
    import webbrowser
    def _open():
        time.sleep(1.2)  # 等 server ready
        # 連到 127.0.0.1 是最可靠的（即使綁 0.0.0.0，瀏覽器也吃 127.0.0.1）
        url = f"http://127.0.0.1:{port}/"
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def main() -> None:
    host = os.environ.get("NVR_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("NVR_WEB_PORT", "8444"))
    debug = os.environ.get("NVR_WEB_DEBUG", "").lower() in ("1", "true")
    # 預設不自動開瀏覽器（避免開發 / 重啟時一直跳分頁干擾）。
    # 想自動開就設 NVR_WEB_OPEN_BROWSER=1。
    auto_open = os.environ.get("NVR_WEB_OPEN_BROWSER", "").lower() in ("1", "true")
    _print_banner(host, port)
    _maybe_open_browser(host, port, auto_open)

    # 信號處理：SIGTERM/SIGINT 結束時，把 daemon scan thread 留下的
    # scan_runs.status='running' 孤兒 row 標為 failed，避免 dashboard 永遠顯示「掃描中」。
    def _shutdown_handler(signum, frame):
        """SIGTERM/SIGINT 時把 DB 內所有 status='running' 的孤兒 row 標為 failed。

        為什麼不只靠 _scan_state：signal handler 可能在 background thread
        已經呼叫 begin_scan_run 但尚未 _finish_scan_state 時觸發，
        此時 _scan_state.run_id 是 None。所以直接用 DB 查詢最穩。
        """
        try:
            import web.db as _webdb
            db_path = _get_db_path(app)
            count = _webdb.mark_all_running_as_interrupted(db_path)
            if count:
                logger.info("SIGTERM/SIGINT: mark %d running scan(s) as interrupted", count)
        except Exception as e:
            logger.warning("shutdown cleanup 失敗: %s", e)
        raise SystemExit(0)

    import signal
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # use_reloader=False 必須（PyInstaller / 雙擊重複 fork 會壞）
    # threaded=True 必須（Werkzeug 預設單 thread，背景 scan thread 跑時
    #   連 accept queue 都會卡死；2026-07-06 親身踩到 — /scan 永不回應）
    # Flask 3.x 用 **options 收集剩餘 kwargs 直傳 run_simple，所以直接列舉
    app.run(
        host=host, port=port,
        debug=debug,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
