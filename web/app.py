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
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, cast
from datetime import datetime, timezone
from pathlib import Path

# Week 6 #017 — 共用 helpers（從本檔抽出到 web/helpers.py）
# 別名兼容：既有程式碼（含測試）仍可從 web.app import _safe_int / _to_taipei_str / TAIPEI_TZ
from web.helpers import (
    TAIPEI_TZ as _TAIPEI_TZ,
    safe_int as _safe_int,
    to_taipei_str as _to_taipei_str,
)

# Module-level logger (server-side, 寫 stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nvr.web")

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    send_file,
    session as flask_session,
    url_for,
)

from web import db as webdb
from web.fleet import (
    get_fleet_view,
    get_camera_health_distribution,
    get_thumbnail_coverage,
    get_system_health,
)

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
    parse_csv as _parse_csv,
    parse_json as _parse_json,
    detect_format as _detect_format,
    CSV_FIELDS as _CSV_FIELDS,
)


# === 共用 DB_PATH 取得（給 routes 用）===
def _get_db_path(app: Flask) -> str:
    return cast(str, app.config["DB_PATH"])


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
# Week 7 Task 5：移除本地未使用的 _parse_nvr_form 重複定義（從 web.nvr_form.parse_nvr_form import 而來）
# === 註：原 line 127 之 _parse_nvr_form 為 dead code，與 line 95 的 import 衝突；實際路由都用 import 的版本 ===
def _parse_nvr_form_unused(form) -> dict:
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


def _probe_one_nvr_online(
    nvr_cfg: dict, user_nonce: str, user_key: str, timeout: int
) -> int:
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
        logger.warning(
            "[dashboard online] NVR %s 探測失敗：%s",
            nvr_cfg.get("name", nvr_cfg.get("host", "?")),
            e,
        )
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


def _register_blueprints(app: Flask) -> None:
    """Week 6 #017 — register 5 個業務領域 blueprint（取代原 _register_routes）。

    Stage B：35 條 URL 全部改由 blueprint 提供；app 內不再 inline @app.route。
    Errorhandler（404/500）仍註冊在 factory 主幹（跨 bp 共用）。

    重要：template 內仍用扁平 endpoint 名稱（`url_for('dashboard')`、
    `url_for('runs_list')` 等 30+ 處）。Blueprint 預設 endpoint 為
    `bp_name.func_name`（如 `dashboard.dashboard`），會炸既有 templates。
    解法：register 後用 `_alias_legacy_endpoints` 把核心 endpoint 攤平。

    註冊順序本身不影響功能；列出順序對應人類閱讀：
        dashboard → runs → nvrs → scan → devices
    """
    from web.blueprints.dashboard_bp import dashboard_bp
    from web.blueprints.runs_bp import runs_bp
    from web.blueprints.nvrs_bp import nvrs_bp
    from web.blueprints.scan_bp import scan_bp
    from web.blueprints.devices_bp import devices_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(nvrs_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(devices_bp)

    _alias_legacy_endpoints(app)

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message=str(e)), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500, message=str(e)), 500


# 既有 templates 內 url_for() 仍用扁平 endpoint 名稱（30+ 處）。
# 為避免一次改 30 個 template（容易引入 XSS / typo），
# 在 factory 內把 bp 內函式 endpoint alias 為扁平名稱。
_LEGACY_ENDPOINT_ALIAS: dict[str, str] = {
    # dashboard_bp
    "dashboard": "dashboard.dashboard",
    "theme_preview": "dashboard.theme_preview",
    "theme_apply": "dashboard.theme_apply",
    "fleet": "dashboard.fleet",
    "dark_toggle": "dashboard.dark_toggle",
    # runs_bp
    "runs_list": "runs.runs_list",
    "run_detail": "runs.run_detail",
    "reports_list": "runs.reports_list",
    "reports_download": "runs.reports_download",
    "adhoc_query": "runs.adhoc_query",
    # nvrs_bp
    "nvrs_list": "nvrs.list_",
    "nvr_toggle_enabled": "nvrs.toggle",
    "nvr_new": "nvrs.new",
    "nvr_edit": "nvrs.edit",
    "nvr_delete": "nvrs.delete",
    "nvr_test_connection": "nvrs.test_connection",
    "nvr_import": "nvrs.import_",
    "nvr_import_template_csv": "nvrs.import_template_csv",
    "nvr_import_template_json": "nvrs.import_template_json",
    "nvr_export_csv": "nvrs.export_csv",
    "nvr_export_json": "nvrs.export_json",
    # scan_bp
    "scan_trigger": "scan.scan_trigger",
    "scan_status": "scan.scan_status",
    "refresh_completeness_trigger": "scan.refresh_completeness_trigger",
    "refresh_completeness_status": "scan.refresh_completeness_status",
    # devices_bp
    "wall": "devices.wall",
    "devices_list": "devices.devices_list",
    "devices_discover": "devices.devices_discover",
    "devices_discover_result": "devices.devices_discover_result",
    "device_detail": "devices.device_detail",
    "camera_health_history": "devices.camera_health_history",
    "trends": "devices.trends",
    "events_list": "devices.events_list",
    "abnormal_list": "devices.abnormal_list",
    "abnormal_export_pdf": "devices.abnormal_export_pdf",
}


def _alias_legacy_endpoints(app: Flask) -> None:
    """給每個 bp 既有 endpoint 加扁平 alias（給既有 templates 用）。

    Flask 內 url_for(endpoint) 是查 url_map 內的 rule.endpoint；
    只塞 view_functions 不夠。所以用 add_url_rule 重複註冊一個同 URL、
    同 view function 的 rule，但 endpoint 用扁平名稱。

    注意：每個 alias rule 不能跟現有 rule endpoint 衝突，所以檢查
    app.view_functions[flat_ep] 是否已存在於既有 rules。
    """
    for flat_ep, qualified_ep in _LEGACY_ENDPOINT_ALIAS.items():
        # 找既有 rule
        target_rule = None
        for rule in app.url_map.iter_rules():
            if rule.endpoint == qualified_ep:
                target_rule = rule
                break
        if target_rule is None:
            raise RuntimeError(
                f"_alias_legacy_endpoints: qualified endpoint {qualified_ep!r} not in url_map"
            )
        view = app.view_functions[qualified_ep]
        # 加 alias rule（如 endpoint 已存在則跳過重複註冊）
        if flat_ep not in app.view_functions:
            rule_methods = target_rule.methods
            if rule_methods is None:
                rule_methods = {"GET"}  # fallback：URL rule 沒指定時預設 GET
            app.add_url_rule(
                target_rule.rule,
                endpoint=flat_ep,
                view_func=view,
                methods=list(rule_methods - {"HEAD", "OPTIONS"}),
            )


def _make_flask_app() -> Flask:
    """建 Flask app，自動處理 PyInstaller 內 templates / static 路徑。

    一般開發：`templates` / `static` 跟 web/app.py 同目錄。
    PyInstaller one-dir：`web/templates` 被抽到 `_internal/web/templates/`，
    需要從 `sys._MEIPASS`（runtime root）絕對路徑重新指。
    """
    import sys

    if getattr(sys, "frozen", False):
        # 打包狀態（onedir）：sys._MEIPASS = dist/web/_internal（templates在 _internal/web/templates）
        meipass = Path(getattr(sys, "_MEIPASS", "."))
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


def create_app(db_path: str | None = None, secret_key: str | None = None) -> Flask:
    """
    Flask app factory。

    Args:
        db_path: SQLite 路徑。None 時從 NVR_DB_PATH env var 或預設 ./nvr_scan.db 讀。
        secret_key: 測試用注入；正式呼叫不傳，強制走 env var 檢查（Day-0 修補 #1）。
    """
    app = _make_flask_app()
    app.config["DB_PATH"] = db_path or os.environ.get("NVR_DB_PATH", "./nvr_scan.db")
    # flash() 需要 SECRET_KEY；強制要求從 env var 注入，無 fallback（Day-0 修補 #1）
    # 沒設 NVR_WEB_SECRET_KEY 就 raise，避免 session forgery
    if secret_key is None:
        secret_key = os.environ.get("NVR_WEB_SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "NVR_WEB_SECRET_KEY 環境變數未設定。" "請參考 .env.example 並設定後重啟。"
        )
    app.config["SECRET_KEY"] = secret_key
    # 確認 DB 存在（避免啟動後第一個 request 才 500）
    if app.config["DB_PATH"] != ":memory:" and not Path(app.config["DB_PATH"]).exists():
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
    _register_blueprints(app)

    # === Week 5 middleware 註冊點（#012-#016，待 PR #3 merge 後啟用）===
    # 5 個 middleware 必須集中在 factory 主幹註冊，不可進入任何 bp。
    # 待 Week 6 主線合併 Week 5 後，由 Plan §D2「保留 module-level」覆寫：
    #   - from web.auth.middleware import register_auth_middleware  # #012
    #   - from web.ratelimit import make_exempt_when_trusted_ip      # #014
    #   - from audit.middleware import register_audit_middleware      # #015
    # 註冊位置選擇：路由前面（讓 before_request 早攔截）但 SECRET_KEY 已設完。
    # 條件：全部以「feature flag 預設關 → pass-through」架構，不影響既有測試。
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
                "last_detected": e.get("occurred_at") or e.get("detected_at"),
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


# === Query string 安全轉 int / 時區 UTC→Taipei 字串 ===
# Week 6 #017：定義已抽至 web/helpers.py；上方 import 區塊以別名 import 確保
# 既有 import `from web.app import _safe_int, _to_taipei_str, TAIPEI_TZ` 仍運作。


def _build_abnormal_pdf(
    groups: list[dict],
    topic_zh: Callable[[str], str],
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
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
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
        "Title",
        parent=styles["Title"],
        fontName=font_bold,
        fontSize=20,
        textColor=colors.HexColor("#1a365d"),
        spaceAfter=8,
        alignment=0,  # left
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        textColor=colors.HexColor("#666666"),
        spaceAfter=4,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName=font_bold,
        fontSize=13,
        textColor=colors.HexColor("#2c5282"),
        spaceBefore=10,
        spaceAfter=4,
        borderPadding=4,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=12,
    )

    # 3. 文件
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
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
        overview_data,
        colWidths=[60 * mm, 80 * mm],
    )
    overview_table.setStyle(
        TableStyle(
            [
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
            ]
        )
    )
    story.append(overview_table)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(run_info, meta_style))

    # 依 NVR 分組
    if not groups:
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                "✓ 目前沒有任何未解決的異常事件。",
                body_style,
            )
        )
    else:
        # 依 NVR 聚合
        nvr_buckets: dict[int, list[dict]] = {}
        for g in groups:
            nvr_buckets.setdefault(g["nvr_id"], []).append(g)

        for nvr_id in nvr_buckets:
            cams = nvr_buckets[nvr_id]
            nvr_name = cams[0]["nvr_name"]
            nvr_total = sum(c["open_count"] for c in cams)
            story.append(
                Paragraph(
                    f"📡 {nvr_name}（{nvr_total} 筆未解決）",
                    section_style,
                )
            )
            # Table：相機名稱 / 故障類型 / 首次 / 最新
            data = [["攝影機", "故障類型", "首次發現", "最新一次"]]
            for c in cams:
                topics_zh = "、".join(topic_zh(t) for t in c["topics"])
                data.append(
                    [
                        c["camera_name"],
                        Paragraph(topics_zh, body_style),
                        _to_taipei_str(c["first_detected"]),
                        _to_taipei_str(c["last_detected"]),
                    ]
                )
            tbl = Table(
                data,
                colWidths=[50 * mm, 60 * mm, 30 * mm, 30 * mm],
                repeatRows=1,
            )
            tbl.setStyle(
                TableStyle(
                    [
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
                        (
                            "INNERGRID",
                            (0, 0),
                            (-1, -1),
                            0.25,
                            colors.HexColor("#e2e8f0"),
                        ),
                        # padding
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        # alternating row bg
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#f7fafc")],
                        ),
                    ]
                )
            )
            story.append(tbl)
            story.append(Spacer(1, 4 * mm))

    # 頁尾
    def _on_page(canv, doc_):
        canv.saveState()
        canv.setFont(font_name, 8)
        canv.setFillColor(colors.HexColor("#999999"))
        canv.drawString(
            18 * mm,
            10 * mm,
            f"NVR Scanner · {gen_at}",
        )
        canv.drawRightString(
            A4[0] - 18 * mm,
            10 * mm,
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
_scan_lock = (
    threading.RLock()
)  # RLock 才能在 scan_trigger「握著鎖呼叫 _reset_scan_state」時不死鎖（2026-07-07）


# Phase 2.8 補：timeline refresh 獨立 state（跟 scan 不互相 block）
_timeline_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "checked": 0,
    "written": 0,
    "errors_count": 0,
    "error": None,
}
_timeline_lock = threading.RLock()


def _reset_timeline_state() -> None:
    """重置 timeline refresh 狀態。"""
    with _timeline_lock:
        _timeline_state.update(
            {
                "running": True,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "finished_at": None,
                "checked": 0,
                "written": 0,
                "errors_count": 0,
                "error": None,
            }
        )


def _finish_timeline_state(success: bool, **kwargs) -> None:
    """結束 timeline refresh 狀態。"""
    with _timeline_lock:
        _timeline_state["running"] = False
        _timeline_state["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        )
        _timeline_state.update(kwargs)
        if not success and "error" not in kwargs:
            _timeline_state["error"] = "timeline refresh failed"


def _reset_scan_state(total_nvrs: int | None = None) -> None:
    """重置 scan 狀態。total_nvrs=None 時保留現值（2026-07-13 Phase 2.2）。"""
    with _scan_lock:
        _scan_state.update(
            {
                "running": True,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "finished_at": None,
                "run_id": None,
                "ok_nvrs": 0,
                "failed_nvrs": 0,
                "abnormal_cameras": 0,
                "error": None,
            }
        )
        if total_nvrs is not None:
            _scan_state["total_nvrs"] = total_nvrs


def _finish_scan_state(success: bool, **kwargs) -> None:
    with _scan_lock:
        _scan_state["running"] = False
        _scan_state["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
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
        from db.sqlite_writer import SqliteWriter

        # 1. 從 DB 讀啟用的 NVR（單一 source of truth）
        enabled = webdb.list_enabled_nvrs(db_path)
        if not enabled:
            _finish_scan_state(
                success=False,
                error="DB 沒有啟用的 NVR（請到 /nvrs 新增並啟用）",
            )
            return
        cfg: dict[str, Any] = {"nvr_servers": enabled, "scan_settings": {"timeout_seconds": 10}}
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
                cfg,
                credentials,
                writer,
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
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )


# Phase 2.8 補：Dashboard 「🔄 重整完整率」按鈕對應的 background worker
# 跟 scan 走獨立 state，scan 跑時 refresh 仍可啟動（不互相 block）。
def _run_timeline_refresh(app: Flask, db_path: str) -> None:
    """背景 thread 跑 24h timeline 收集（每台 NVR 逐台跑 _timeline_check_loop）。

    跟 batch_scan 不同：
      - 不跑 events 收集、不跑 image health（單純 timeline）
      - 開一個獨立 scan_run（recording_status 寫入需 transaction）
      - 跑完 finish_scan_run（會出現在 /runs 列表中）
    """
    try:
        # 函式內 import（避免 module-load 階段就 import batch_scan / nvr_scanner）
        from batch_scan import _timeline_check_loop
        from nvr_scanner import AvigilonScanner
        from db.sqlite_writer import SqliteWriter

        # 1. 從 DB 讀啟用的 NVR
        enabled = webdb.list_enabled_nvrs(db_path)
        if not enabled:
            _finish_timeline_state(
                success=False,
                error="DB 沒有啟用的 NVR（請到 /nvrs 新增並啟用）",
            )
            return

        # 2. 認證（沿用 .env 的 AVIGILON_*）
        user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
        user_key = os.environ.get("AVIGILON_USER_KEY", "")
        if not user_nonce or not user_key:
            _finish_timeline_state(
                success=False,
                error="伺服器未設定 AVIGILON_USER_NONCE/KEY（檢查 .env）",
            )
            return
        credentials = {
            "user_nonce": user_nonce,
            "user_key": user_key,
            "integration_id": os.environ.get("AVIGILON_INTEGRATION_ID", ""),
            "username_override": os.environ.get("AVIGILON_USERNAME") or None,
            "password_override": os.environ.get("AVIGILON_PASSWORD") or None,
        }

        # 3. 開 scan_run（recording_status 寫入需要 transaction）
        total_checked = 0
        total_written = 0
        total_errors = 0

        with SqliteWriter(db_path) as writer:
            rid = writer.begin_scan_run(
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

            for nvr in enabled:
                nvr_int_id = writer.upsert_nvr(nvr)
                try:
                    # per-NVR instance（HTTP session 絕不跨 NVR 共用）
                    scanner = AvigilonScanner(
                        nvr,
                        user_nonce=credentials.get("user_nonce") or "",
                        user_key=credentials.get("user_key") or "",
                        integration_id=credentials.get("integration_id") or "",
                        timeout=10,
                    )
                    scanner.login()  # _timeline_check_loop 直接呼叫 get_timeline，不經過 scan() → 不會自動登入
                    summary = _timeline_check_loop(
                        scanner,
                        nvr_int_id,
                        writer,
                        verbose=True,
                    )
                    total_checked += summary["checked"]
                    total_written += summary["written"]
                    total_errors += len(summary["errors"])
                except Exception as exc:
                    total_errors += 1
                    logger.warning(
                        "timeline refresh %s 失敗：%s: %s",
                        nvr.get("id"),
                        type(exc).__name__,
                        exc,
                    )

            writer.finish_scan_run(
                rid,
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                status="complete",
                stats={
                    "total_cameras": total_checked,
                    "abnormal_cameras": 0,
                    "total_nvrs": len(enabled),
                    "ok_nvrs": len(enabled) - total_errors,
                    "failed_nvrs": total_errors,
                },
            )

        _finish_timeline_state(
            success=True,
            checked=total_checked,
            written=total_written,
            errors_count=total_errors,
        )
    except Exception as exc:
        logger.exception("timeline refresh failed: %s", exc)
        _finish_timeline_state(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )


# === 預設 app（給 flask run / python -m web.app 用）===
# Day-0 修補 #1 延伸：create_app() 現在會 raise（沒設 SECRET_KEY），
# module-level 直接呼叫會讓測試 import 時爆炸。改用 lazy proxy：
# 第一次存取才建，main() 內已先設好 NVR_WEB_SECRET_KEY。
_app_singleton: Flask | None = None


def __getattr__(name: str):
    """PEP 562 lazy attribute：讓 `from web.app import app` 不在 import 時就建 app。"""
    global _app_singleton
    if name == "app":
        if _app_singleton is None:
            _app_singleton = create_app()
        return _app_singleton
    raise AttributeError(f"module 'web.app' has no attribute {name!r}")


# === CLI 入口 ===
def _print_banner(host: str, port: int, db_path: str | None = None) -> None:
    """印 banner：本機 + 所有 LAN IP 的 URL，方便員工看。

    0.0.0.0 對員工沒意義，要列 127.0.0.1 + 實際 NIC IP。
    """
    print("=" * 64)
    print("[INFO] NVR Web UI 已啟動")
    print(f"[INFO] 綁定：{host}:{port}")
    print(f"[INFO] 資料庫：{db_path or '(default)'}")
    print()
    print("  本機存取：")
    print(f"    http://127.0.0.1:{port}/")
    print()
    print("  LAN 內其他電腦存取（任一皆可）：")
    if host in ("0.0.0.0", ""):
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
            raw_ip: Any = info[4][0]
            # socket.getaddrinfo 可能回 IPv4 字串或 IPv6；只接受字串型 IPv4
            ip = raw_ip if isinstance(raw_ip, str) else ""
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
    host = os.environ.get("NVR_WEB_HOST", "127.0.0.1")  # Day-0: 預設只綁本機
    port = int(os.environ.get("NVR_WEB_PORT", "8444"))
    debug = os.environ.get("NVR_WEB_DEBUG", "").lower() in ("1", "true")
    # 取得 Flask app：使用 module-level `app` 變數（PEP 562 lazy proxy）。
    # 測試可透過 `webapp.app = fake` 注入；生產環境 lazy proxy 在第一次存取時建立。
    import web.app as _webapp_mod

    app = _webapp_mod.app
    # 預設不自動開瀏覽器（避免開發 / 重啟時一直跳分頁干擾）。
    # 想自動開就設 NVR_WEB_OPEN_BROWSER=1。
    auto_open = os.environ.get("NVR_WEB_OPEN_BROWSER", "").lower() in ("1", "true")
    _print_banner(host, port, app.config["DB_PATH"])
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
                logger.info(
                    "SIGTERM/SIGINT: mark %d running scan(s) as interrupted", count
                )
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
        host=host,
        port=port,
        debug=debug,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
