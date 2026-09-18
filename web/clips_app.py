"""
web/clips_app.py
================
Phase 2.7 — 錄影回放調閱 Web UI（給其他部門用）。

啟動：python -m web.clips_app
預設：http://127.0.0.1:8555（與 v2 Web UI 8444 分開）

環境變數：
    NVR_DB_PATH      SQLite 檔案路徑（預設 ./nvr_scan.db；同 web.app）
    NVR_CLIPS_HOST   綁定 IP（預設 0.0.0.0；LAN 友善）
    NVR_CLIPS_PORT   埠號（預設 8555，user 指定 — 避開 NVR 8443）
    NVR_CLIPS_DEBUG  True/False（預設 False）
    NVR_CLIPS_CLIENT 測試用注入點（"mock" = 走 MockMediaClient，預設 = real placeholder）

與 NVR 的通訊：使用 AvigilonScanner 拿 session token，再傳給 MediaApiClient
（見 web/clip_retrieval.py）。
"""
from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from flask import (
    Flask, abort, jsonify, redirect, render_template, request, Response,
    session as flask_session, stream_with_context, url_for,
)

from web import db as webdb

# Protocol + 兩個實作
from web.clip_retrieval import (
    MediaApiClient, MockMediaClient, MpdMediaClient,
    NvrAuthError, NvrInternalError, NvrNoRecordingError,
)

# NVR CRUD Blueprint（Phase 2.7 補：讓 clips app 自帶 NVR 管理）
from web.nvr_routes import nvr_bp


# Module-level logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nvr.clips")
# 2026-08-06 fix11.txt：fmp4 stream wall time cap。NVR 卡死不吐 bytes 時
# （user 040/041：CamA 5 分鐘沒回應），超過此秒就 break generator、回 partial bytes，
# frontend <video> 可播（partial mp4 is valid container）。
_MAX_FETCH_WALL_SECONDS = 30.0  # default；可從 env NVR_MAX_FETCH_WALL_SECONDS 覆寫
import os as _os_wall
if _os_wall.environ.get("NVR_MAX_FETCH_WALL_SECONDS"):
    try:
        _MAX_FETCH_WALL_SECONDS = float(_os_wall.environ["NVR_MAX_FETCH_WALL_SECONDS"])
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# 啟動時 load .env（跟 web.app 一致 — 否則 login() 拿不到 user_nonce/key）
# ---------------------------------------------------------------------------
def _bootstrap_env() -> None:
    try:
        from nvr_scanner import load_env_file
    except ImportError:
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            loaded = load_env_file(env_path)
            if loaded:
                logger.info("已從 .env 載入 %d 個環境變數", loaded)
        except Exception as e:
            logger.warning(".env 載入失敗：%s", e)


_bootstrap_env()


# ---------------------------------------------------------------------------
# In-memory session token cache（per-NVR，TTL 30 分鐘）
# ---------------------------------------------------------------------------
# 2026-08-06：NVR 「no stale cache」 trust cache — 若某 NVR 探測一輪無 stale，
# 在 _TRUST_TTL 秒內 skip probe（節省每 cam × 3 anchor × fmp4 head 的 12 個 NVR requests）。
# 存在 app.config["NO_STALE_TRUST"]（test 易隔離）；無 config 時 fallback module 級 dict。
_NO_STALE_TRUST_DEFAULT: dict[int, float] = {}  # fallback for 8555 主 process
_NO_STALE_TRUST_TTL = 600  # 10 分鐘

class _SessionStore:
    """Avigilon session token 快取。Key = NVR 內部 id（int）。"""

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[int, tuple[str, float]] = {}

    def get(self, internal_id: int) -> str | None:
        with self._lock:
            entry = self._store.get(internal_id)
            if not entry:
                return None
            token, ts = entry
            if time.time() - ts > self._ttl:
                self._store.pop(internal_id, None)
                return None
            return token

    def set(self, internal_id: int, token: str) -> None:
        with self._lock:
            self._store[internal_id] = (token, time.time())

    def clear(self, internal_id: int | None = None) -> None:
        with self._lock:
            if internal_id is None:
                self._store.clear()
            else:
                self._store.pop(internal_id, None)


# ---------------------------------------------------------------------------
# 並行 snapshot 抓取 + Pillow 縮圖壓縮
# ---------------------------------------------------------------------------
# 2026-07-29（Wall 縮圖重構）：抽出共用至 web.snapshot.compress_to_thumbnail。
# 8555 clips 與 8444 image_health loop 共用同一個 size / quality，避免漂移。
from web.snapshot import compress_to_thumbnail as _compress_to_thumbnail  # noqa: F401


def _fetch_snapshot_with_meta(
    client: MediaApiClient,
    camera_id: str,
    at_time: datetime,
) -> dict:
    """單台相機：抓 jpeg → 縮圖 → base64。回傳 dict（含錯誤訊息）。"""
    try:
        raw = client.get_snapshot(camera_id, at_time)
        thumb = _compress_to_thumbnail(raw)
        return {
            "camera_id": camera_id,
            "ok": True,
            "jpeg_b64": base64.b64encode(thumb).decode("ascii"),
            "raw_size": len(raw),
            "thumb_size": len(thumb),
        }
    except Exception as e:
        return {
            "camera_id": camera_id,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }


def fetch_snapshots_parallel(
    client: MediaApiClient,
    camera_ids: list[str],
    at_time: datetime,
    *,
    max_workers: int = 8,
) -> list[dict]:
    """多台相機並行抓 snapshot。回傳 list（順序對齊 camera_ids）。"""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_fetch_snapshot_with_meta, client, cid, at_time): cid
            for cid in camera_ids
        }
        for fut in as_completed(futures):
            cam_id = futures[fut]
            exc = fut.exception()
            if exc is not None:
                results[cam_id] = {
                    "camera_id": cam_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            else:
                results[cam_id] = fut.result()
    # 保持原本 camera_ids 順序
    return [results[cid] for cid in camera_ids if cid in results]


# ---------------------------------------------------------------------------
# Login 與 client factory
# ---------------------------------------------------------------------------
def _login_nvr(nvr_row: dict) -> str:
    """用 AvigilonScanner.login() 拿 session token。失敗拋例外。

    需要 .env 內已設 AVIGILON_USER_NONCE / AVIGILON_USER_KEY（整合帳號）。
    """
    from nvr_scanner import AvigilonScanner, get_credential

    user_nonce = get_credential("AVIGILON_USER_NONCE", "AVIGILON_USER_NONCE", hide=False)
    user_key = get_credential("AVIGILON_USER_KEY", "AVIGILON_USER_KEY", hide=True)

    # AvigilonScanner 需要 nvr_config dict + user_nonce/user_key
    # （不是 host= kwarg；2026-07-07 修）
    nvr_config = {
        "id": nvr_row["nvr_id"],
        "name": nvr_row["name"],
        "host": nvr_row["host"],
        "port": nvr_row.get("port", 8443),
        "username": nvr_row.get("username"),
        "password": nvr_row.get("password"),
        "verify_ssl": bool(nvr_row.get("verify_ssl", 0)),
    }
    scanner = AvigilonScanner(
        nvr_config,
        user_nonce=user_nonce,
        user_key=user_key,
        verify_ssl=bool(nvr_row.get("verify_ssl", 0)),
    )
    return scanner.login()


def get_client_for_nvr(
    nvr_row: dict,
    *,
    session_token: str | None = None,
) -> MediaApiClient:
    """依環境變數決定回 Mock 或 Mpd 客戶端。

    Args:
        nvr_row: webdb.get_nvr(...) 回傳的 dict（含 host/port/verify_ssl）
        session_token: 已登入的 token；若 None 會自己 login（用 session cache）
    """
    use_mock = os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock"
    if use_mock:
        return MockMediaClient()

    # 真實 MPD 客戶端（佔位實作 — 等 NVR 上線驗證）
    return MpdMediaClient(
        host=nvr_row["host"],
        port=nvr_row.get("port", 8443),
        session=session_token or "",
        verify_ssl=bool(nvr_row.get("verify_ssl", 0)),
    )


def get_session_for_nvr(
    internal_id: int,
    session_store: _SessionStore,
) -> str:
    """從 session_store 拿 token；過期或缺則重 login。"""
    token = session_store.get(internal_id)
    if token:
        return token
    nvr_row = webdb.get_nvr(os.environ.get("NVR_DB_PATH", "./nvr_scan.db"), internal_id)
    if not nvr_row:
        raise ValueError(f"找不到 NVR internal_id={internal_id}")
    token = _login_nvr(nvr_row)
    session_store.set(internal_id, token)
    return token


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    # 用獨立的 template folder，避免跟 8444 web.app 共用 `web/templates/`
    # 8444 跟 8555 各自獨立打包後，需要完全分離 template
    template_folder=os.path.join(os.path.dirname(__file__), "clips_templates"),
)
app.config["SESSION_STORE"] = _SessionStore()  # 測試可換成 in-memory 或 mock

# Dark Mode 需要的 SECRET_KEY（與 8444 區隔，確保 cookie 不互通）
app.config["SECRET_KEY"] = os.environ.get(
    "NVR_CLIPS_SECRET_KEY", "nvr-clips-dev-key-change-in-prod",
)

# NVR Blueprint 需要讀 DB_PATH；跟 _get_db_path() 同來源
app.config["DB_PATH"] = os.environ.get("NVR_DB_PATH", "./nvr_scan.db")
app.register_blueprint(nvr_bp)


# === Spec F：錄影覆蓋熱區（/clips/coverage）===
from web.coverage import fetch_coverage_from_nvr
from web.db import get_nvr as _get_nvr, list_cameras_for_nvr as _list_cameras_for_nvr


def _build_nvr_config(nvr_row: dict) -> dict:
    """從 webdb.get_nvr() row 轉成 AvigilonScanner 預期的 nvr_config dict。"""
    return {
        "id": nvr_row["nvr_id"],
        "name": nvr_row["name"],
        "host": nvr_row["host"],
        "port": nvr_row.get("port", 8443),
        "username": nvr_row.get("username"),
        "password": nvr_row.get("password"),
        "verify_ssl": bool(nvr_row.get("verify_ssl", 0)),
    }


@app.route("/clips/coverage")
def clips_coverage():
    """錄影覆蓋熱區頁面（給 1 台 NVR 看所有 cam 24h 錄影時間軸）。"""
    return render_template("coverage.html")


@app.route("/clips/coverage/data")
def clips_coverage_data():
    """JSON API：回傳 1 台 NVR 所有 cam 的 timeline 資料。

    Spec F 合規（2026-08-04）：
    1. 認證 env 顯式檢查（不允許 fallback 到互動 prompt，否則 HTTP request 會卡 stdin）
    2. ISO 8601 字串 parse 成 datetime 後比較（不用字串比對，避免時區 / 字典序錯誤）
    3. NVR 連線失敗嚴格回 502（不再 silent fallback）
    """
    from nvr_scanner import AvigilonScanner, get_credential

    # 1. 認證 env 顯式檢查 — 缺就回 500（伺服器設定錯誤，不是 client 請求錯誤）
    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    if not user_nonce or not user_key:
        return jsonify({
            "error": "伺服器未設定 AVIGILON_USER_NONCE / AVIGILON_USER_KEY（請檢查 .env）"
        }), 500

    # 2. parse nvr_id
    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    if not internal_id:
        return jsonify({"error": "缺少 nvr_id"}), 400

    # 3. parse ISO 8601 時間（不靠字串比對；錯誤回 400 而非 502）
    start_iso = request.args.get("start", "")
    end_iso = request.args.get("end", "")
    if not start_iso or not end_iso:
        return jsonify({"error": "缺少 start / end"}), 400
    try:
        # 接受 "2026-08-04T00:00:00Z" 或 "...+00:00"；統一把 Z 換成 +00:00
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return jsonify({"error": "start / end 必須是 ISO 8601 格式"}), 400
    if end_dt <= start_dt:
        return jsonify({"error": "end 必須大於 start"}), 400

    # 4. 抓 NVR row
    nvr_row = _get_nvr(_get_db_path(), internal_id)
    if nvr_row is None:
        return jsonify({"error": f"找不到 NVR id={internal_id}"}), 404

    # 5. 抓 cam 清單
    cams = _list_cameras_for_nvr(_get_db_path(), internal_id)
    if not cams:
        return jsonify({"error": "該 NVR 沒有 cam"}), 404

    # 6. 抓取 timeline（env 已驗證過，下面 get_credential 不会再卡 stdin）
    try:
        # 1. login 先（會用 env var 裡的 NONCE/KEY + nvr_row 內的帳密）
        session_token = _login_nvr(nvr_row)

        # 2. 建 scanner 物件，手動塞 token（跳過第二次 login()）
        scanner = AvigilonScanner(
            _build_nvr_config(nvr_row),
            user_nonce=get_credential("AVIGILON_USER_NONCE", "AVIGILON_USER_NONCE", hide=False),
            user_key=get_credential("AVIGILON_USER_KEY", "AVIGILON_USER_KEY", hide=True),
            verify_ssl=bool(nvr_row.get("verify_ssl", 0)),
        )
        scanner._session_token = session_token

        def fetch_one(cam_id: str, s: str, e: str) -> dict:
            return scanner.get_timeline(cam_id, from_iso=s, to_iso=e)

        out = fetch_coverage_from_nvr(
            nvr={
                "host": nvr_row["host"],
                "port": nvr_row["port"],
                "nvr_id": nvr_row.get("nvr_id", ""),
            },
            cameras=[
                {"device_id": c["device_id"], "camera_name": c.get("name", c["device_id"])}
                for c in cams
            ],
            start_iso=start_iso,
            end_iso=end_iso,
            timeline_fetcher=fetch_one,
        )
    except Exception as e:
        # 7. 連線 / login / fetch 任何失敗 → 502（NVR 端錯誤）
        logger.error("/clips/coverage/data 抓取 NVR 失敗 nvr_id=%d: %s", internal_id, e)
        return jsonify({"error": f"抓取 NVR 失敗: {e}"}), 502

    return jsonify(out)


def _get_db_path() -> str:
    return os.environ.get("NVR_DB_PATH", "./nvr_scan.db")


# === Dark Mode 支援 ===

@app.context_processor
def _inject_theme():
    """把 session['dark'] 注入所有 template（給 dark toggle 用）。"""
    return dict(dark=flask_session.get("dark", False))


@app.context_processor
def _inject_dashboard_url():
    """Spec G Batch C Task 11：注入 8444 dashboard URL 給 coverage.html JS 用。

    8555 clips_app 跟 8444 web.app 跨 port；coverage.html 不能用相對路徑 /trends
    （會打到 8555/trends，該路徑不存在）。

    從 env NVR_DASHBOARD_URL 讀（預設 http://127.0.0.1:8444）；可用於 LAN 部署時
    把 8444 host:port 換成對外網址。
    """
    base = os.environ.get("NVR_DASHBOARD_URL", "http://127.0.0.1:8444").rstrip("/")
    return dict(dashboard_url=base)


@app.route("/dark/toggle", methods=["POST"])
def dark_toggle():
    """切換深色模式（純 server-side，redirect 回來源頁）。"""
    flask_session["dark"] = not flask_session.get("dark", False)
    return redirect(request.referrer or url_for("index"))


# === 路由 ===

@app.route("/")
def index():
    return render_template("clips.html")


@app.route("/clips")
def clips_page():
    return render_template("clips.html")


@app.route("/clips/nvrs")
def clips_nvrs():
    """JSON：所有 NVR 清單（給 dropdown 用）。"""
    rows = webdb.get_nvrs(_get_db_path())
    return jsonify([
        {
            "internal_id": r["id"],
            "nvr_id": r["nvr_id"],
            "name": r["name"],
            "host": r["host"],
            "camera_count": r.get("camera_count", 0),
        }
        for r in rows
    ])


@app.route("/clips/cameras")
def clips_cameras():
    """?nvr_id=<internal_id> → JSON cameras list"""
    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    if not internal_id:
        return jsonify({"error": "缺少 nvr_id"}), 400
    cams = webdb.list_cameras_for_nvr(_get_db_path(), internal_id)
    return jsonify(cams)


@app.route("/clips/snapshots")
def clips_snapshots():
    """並行抓 N 台相機的 snapshot，縮圖後 JSON 回傳。

    Query: ?nvr_id=<int>&t=<ISO8601 UTC>
    Returns: [{"camera_id": "...", "ok": bool, "jpeg_b64": "..." | "error": "..."}, ...]
    """
    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    t_str = request.args.get("t", "")
    if not internal_id or not t_str:
        return jsonify({"error": "缺少 nvr_id 或 t"}), 400

    # 解析 ISO 8601 → datetime
    try:
        # 接受 "2026-07-06T12:00:00Z" 或 "...+00:00"
        if t_str.endswith("Z"):
            t_str = t_str[:-1] + "+00:00"
        at_time = datetime.fromisoformat(t_str)
        if at_time.tzinfo is None:
            at_time = at_time.replace(tzinfo=timezone.utc)
    except ValueError as e:
        return jsonify({"error": f"t 解析失敗：{e}"}), 400

    nvr_row = webdb.get_nvr(_get_db_path(), internal_id)
    if not nvr_row:
        return jsonify({"error": f"找不到 NVR internal_id={internal_id}"}), 404

    cams = webdb.list_cameras_for_nvr(_get_db_path(), internal_id)
    if not cams:
        return jsonify({"warning": "此 NVR 沒有相機", "snapshots": []})

    # 拿 session（cache 命中就 skip login）。Mock client 不需要真 token。
    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock":
        session_token = "MOCK-SESSION"
    else:
        try:
            session_token = get_session_for_nvr(internal_id, app.config["SESSION_STORE"])
        except Exception as e:
            logger.error("login 失敗：%s", e)
            return jsonify({"error": f"login 失敗：{e}"}), 502

    client = get_client_for_nvr(nvr_row, session_token=session_token)
    # device_id 是 NVR 端 identifier（給 Media API 用）；name 是給人看的顯示名
    # 支援 ?camera_ids=A,B,C 過濾（前端多選）；不帶就抓全部
    selected = request.args.get("camera_ids", "").strip()
    if selected:
        wanted = set(s.strip() for s in selected.split(",") if s.strip())
        cams = [c for c in cams if c["device_id"] in wanted]
    camera_ids = [c["device_id"] for c in cams]
    id_to_name = {c["device_id"]: c["name"] for c in cams}
    snapshots = fetch_snapshots_parallel(client, camera_ids, at_time)
    # 把 camera_name 補進去（給 UI 顯示，不依賴前端自己 join）
    for s in snapshots:
        s["camera_name"] = id_to_name.get(s.get("camera_id", ""), s.get("camera_id", ""))
    return jsonify({
        "nvr_id": internal_id,
        "t": at_time.isoformat(),
        "camera_count": len(camera_ids),
        "snapshots": snapshots,
    })


@app.route("/clips/fetch", methods=["POST"])
def clips_fetch():
    """POST {nvr_id, camera_id, start, end} → stream fragmented MP4 bytes。

    擴搜邏輯（2026-07-07）：
    1. 先用原 start 試一次 get_recording_duration
    2. 若 < 1s → 回 404 + NO_RECORDING
    3. 若 < 30s → 自動往外擴（start 往前 / end 往後各 +5s）直到湊到 ≥ 30s
       或擴到邊界為止；最壞情況下 NVR 只給 8s 也照實播
    4. 真正的 start / end 透過 X-Actual-Start / X-Actual-End / X-Actual-Duration
       response header 回傳給前端顯示
    """
    from datetime import timedelta
    import time as _time_cf
    _t_cf = {"mpd": 0.0, "fetch": 0.0}
    _t_cf_total0 = _time_cf.monotonic()
    payload = request.get_json(silent=True) or {}
    try:
        internal_id = int(payload.get("nvr_id", 0))
        camera_id = str(payload.get("camera_id", "")).strip()
        start = datetime.fromisoformat(payload["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload["end"].replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"參數錯誤：{e}"}), 400
    if not internal_id or not camera_id:
        return jsonify({"error": "缺少 nvr_id 或 camera_id"}), 400

    nvr_row = webdb.get_nvr(_get_db_path(), internal_id)
    if not nvr_row:
        return jsonify({"error": f"找不到 NVR internal_id={internal_id}"}), 404

    # Mock client 不需要真 token；真實 client 才走 login
    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock":
        session_token = "MOCK-SESSION"
    else:
        try:
            session_token = get_session_for_nvr(internal_id, app.config["SESSION_STORE"])
        except Exception as e:
            logger.error("login 失敗：%s", e)
            return jsonify({"error": f"login 失敗：{e}"}), 502

    client = get_client_for_nvr(nvr_row, session_token=session_token)
    target_seconds = (end - start).total_seconds()  # 通常 30s

    logger.info(
        "[fetch] request: cam=%s nvr=%d req_start=%s req_end=%s target=%.1fs",
        camera_id, internal_id, start.isoformat(), end.isoformat(), target_seconds,
    )

    # === 擴搜：把 start 往前 / end 往後各 +5s，直到湊滿 target ===
    actual_start = start
    actual_end = end
    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() != "mock":
        # mock 模式不擴搜（沒真 MPD），直接照原 start..end
        step = timedelta(seconds=5)
        max_iters = int(target_seconds / 5) + 2  # 最多擴這麼多輪
        try:
            for _ in range(max_iters):
                dur = client.get_recording_duration(camera_id, actual_start)
                if dur <= 0.5:
                    # 這個 seek 點根本沒錄影
                    break
                # 把可用範圍覆蓋 actual_start..actual_start+dur
                coverage_end = actual_start + timedelta(seconds=dur)
                actual_end = coverage_end
                actual_seconds = (actual_end - actual_start).total_seconds()
                if actual_seconds >= target_seconds:
                    break  # 湊滿了
                # 還不夠，把 actual_start 往前推一個 step
                actual_start = actual_start - step
        except Exception as e:
            logger.warning("擴搜失敗（繼續用原 start..end）：%s", e)
            actual_start = start
            actual_end = end

    actual_seconds = (actual_end - actual_start).total_seconds()
    logger.info(
        "[fetch] expanded: cam=%s actual_start=%s actual_end=%s actual_seconds=%.2f target=%.1fs",
        camera_id, actual_start.isoformat(), actual_end.isoformat(),
        actual_seconds, target_seconds,
    )
    _t_cf["mpd"] = _time_cf.monotonic() - _t_cf_total0
    if actual_seconds <= 0.5:
        return jsonify({
            "error": "NO_RECORDING",
            "message": f"此時段無錄影（{start.isoformat()} ~ {end.isoformat()}）",
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
        }), 404

    filename = f"{nvr_row['nvr_id']}_{camera_id}_{int(actual_start.timestamp())}.mp4"

    # 2026-07-14 修架構（fix07.txt）：
    # 原本用 stream_with_context 包 generator，但 generator 在 yield 之前的
    # RuntimeError（例如 NVR fmp4 500）會被 Flask 當未捕獲例外處理，回 HTML error page，
    # 前端 r.json() 收到 "<!doctype..." → 「Unexpected token '<'」完全看不出原因。
    # 修法：把 fetch_clip (generator) 用 list() / b"".join() 一次性讀完，這樣
    # try/except 能正常 catch，回 JSON 給前端看得懂的錯誤訊息。
    # 30s clip ≈ 16MB → 進 memory 可接受。
    from web.clip_retrieval import (
        NvrNoRecordingError, NvrAuthError, NvrInternalError,
    )
    try:
        _t_fetch_start = _time_cf.monotonic()
        body = b"".join(client.fetch_clip(camera_id, actual_start, actual_end, max_wall_seconds=_MAX_FETCH_WALL_SECONDS))
        _t_cf["fetch"] = _time_cf.monotonic() - _t_fetch_start
        logger.info(
            "[fetch] fetched: cam=%s bytes=%d actual_start=%s actual_end=%s",
            camera_id, len(body), actual_start.isoformat(), actual_end.isoformat(),
        )
    except NvrNoRecordingError as e:
        # NVR 404 → 該時段無錄影；友善灰色訊息（前端顯示「📭 此時段無錄影資料」）
        logger.info("/clips/fetch NO_RECORDING：%s", e)
        return jsonify({
            "error": "NO_RECORDING",
            "message": "此時段無錄影資料",
            "stage": "nvr_404",
            "nvr_id": nvr_row["nvr_id"],
            "camera_id": camera_id,
            "actual_start": actual_start.isoformat(),
            "actual_end": actual_end.isoformat(),
        }), 404
    except NvrAuthError as e:
        # NVR 401/403 → session 過期（前端顯示 ⚠️「NVR 認證失敗」）
        logger.warning("/clips/fetch AUTH_FAILED：%s", e)
        return jsonify({
            "error": "AUTH_FAILED",
            "message": "NVR 認證失敗（session 過期）",
            "detail": str(e),
            "stage": "nvr_auth",
            "nvr_id": nvr_row["nvr_id"],
            "camera_id": camera_id,
        }), 502
    except NvrInternalError as e:
        # NVR 5xx / 其他 → NVR 內部錯誤（前端顯示 ⚠️「NVR 連線失敗」）
        logger.error("/clips/fetch NVR_INTERNAL_ERROR：%s", e)
        return jsonify({
            "error": "NVR_INTERNAL_ERROR",
            "message": "NVR 內部錯誤",
            "detail": str(e),
            "stage": "fetch_clip",
            "nvr_id": nvr_row["nvr_id"],
            "camera_id": camera_id,
            "actual_start": actual_start.isoformat(),
            "actual_end": actual_end.isoformat(),
        }), 502
    except RuntimeError as e:
        # 2026-07-14：generic RuntimeError 不是新三種分類時，fallback 為 NVR_INTERNAL_ERROR
        # （保持與 fix07.txt 之前行為相容：500 HTML → 502 JSON 但都是 NVR 端問題）
        logger.error("/clips/fetch NVR_INTERNAL_ERROR (legacy RuntimeError)：%s", e)
        return jsonify({
            "error": "NVR_INTERNAL_ERROR",
            "message": "NVR 內部錯誤",
            "detail": str(e),
            "stage": "fetch_clip_legacy",
            "nvr_id": nvr_row["nvr_id"],
            "camera_id": camera_id,
        }), 502
    except Exception as e:  # 任何其他 IO/解碼問題（非 NVR 端錯誤）
        logger.exception("/clips/fetch 未預期錯誤")
        return jsonify({
            "error": "SERVER_ERROR",
            "message": f"伺服器錯誤：{type(e).__name__}: {e}",
            "stage": "fetch_clip",
        }), 500

    if not body:
        # 抓到 0 bytes（罕見但 NVR 健康但該時段真的沒資料會這樣）
        return jsonify({
            "error": "EMPTY_CLIP",
            "message": "此時段無錄影資料",
            "stage": "empty_body",
            "actual_start": actual_start.isoformat(),
            "actual_end": actual_end.isoformat(),
        }), 404

    return Response(
        body,
        mimetype="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Camera-Id": camera_id,
            "X-Nvr-Id": str(internal_id),
            "X-Actual-Start": actual_start.isoformat(),
            "X-Actual-End": actual_end.isoformat(),
            "X-Actual-Duration": f"{actual_seconds:.2f}",
            "X-Requested-Duration": f"{target_seconds:.2f}",
            "X-Truncated": "true" if actual_seconds < target_seconds else "false",
            "Content-Length": str(len(body)),
            # 2026-08-06 perf：分階段時序，瀏覽器 DevTools Network > Timing 直接讀
            "X-Server-Timing": ", ".join(
                f"{phase};dur={t * 1000:.1f}"
                for phase, t in _t_cf.items()
            ) + f", total;dur={(_time_cf.monotonic() - _t_cf_total0) * 1000:.1f}",
        },
    )
    logger.info(
        "[fetch timing] total=%.2fs mpd=%.2fs fetch=%.2fs cam=%s bytes=%d",
        _time_cf.monotonic() - _t_cf_total0,
        _t_cf["mpd"], _t_cf["fetch"],
        camera_id, len(body),
    )


# ---------------------------------------------------------------------------
# 2026-07-15：NVR stale cache 探測
# ---------------------------------------------------------------------------
def _probe_nvr_stale_cache(
    client,
    camera_id: str,
    t_center,
    anchor_offsets: tuple[int, ...] = (-60, 0, 60),
    head_bytes: int = 8 * 1024,
) -> tuple[bool, list[str]]:
    """檢查 NVR Media API 是否對同一 cam 在多個相鄰時段回傳完全相同的 mp4 bytes。

    背景（2026-07-15 user bug）：cam 1 在今日下午多個時段的 fmp4 response bytes
    md5 完全一樣，但每個時段的 MPD 都回「有錄影」（dur ≈ 8.43s）。這是 NVR 內部
    cache 把請求 seek 到同一段 mp4，是 NVR 端怪行為 — 但對 user 來說，server
    不該把這種「stale bytes」當合法回應轉送出去。

    探測法：
    1. 在 t_center ±N 秒各 query 一段 fmp4（每段只 head head_bytes 算 partial md5）
    2. 若 anchor_offsets 內所有 anchor 的 partial md5 完全一樣 → 視為 stale（true）
    3. 若有任何 anchor MPD 報 0（真的無錄影），不算 stale（讓 query_cam_availability
       階段的 dur==0 機制處理）

    Returns:
        (is_stale, evidence_lines)
        is_stale: True 代表 NVR 在這些時段回傳完全相同 bytes
        evidence_lines: 給 server log 看（例：'anchor -60s md5=abcd1234 bytes=8192'）
    """
    evidence: list[str] = []
    md5_by_anchor: dict[int, str] = {}
    error_anchors: list[int] = []
    import hashlib as _hashlib
    for off in anchor_offsets:
        probe_t = t_center + timedelta(seconds=off)
        try:
            dur = client.get_recording_duration(camera_id, probe_t)
        except Exception as e:
            error_anchors.append(off)
            evidence.append(f"anchor {off:+d}s: MPD query error: {type(e).__name__}: {e}")
            continue
        if dur <= 0.5:
            error_anchors.append(off)
            evidence.append(f"anchor {off:+d}s: dur={dur:.2f}s (<= 0.5, 視為無錄影)")
            continue
        try:
            head = b""
            for chunk in client.fetch_clip(
                camera_id, probe_t, probe_t + timedelta(seconds=1),
                target_seconds=1,
            ):
                head += chunk
                if len(head) >= head_bytes:
                    break
        except Exception as e:
            error_anchors.append(off)
            evidence.append(f"anchor {off:+d}s: fetch_clip error: {type(e).__name__}: {e}")
            continue
        head = head[:head_bytes]
        m = _hashlib.md5(head).hexdigest()[:10]
        md5_by_anchor[off] = m
        evidence.append(f"anchor {off:+d}s: dur={dur:.2f}s bytes={len(head)} md5={m}")
    # 至少要 2 個 anchor 成功 query 才能判斷
    if len(md5_by_anchor) < 2:
        return False, evidence
    distinct = set(md5_by_anchor.values())
    is_stale = len(distinct) == 1
    if is_stale:
        evidence.append(
            f"==> STALE: {len(md5_by_anchor)} anchors 全回同一 bytes（md5={next(iter(distinct))}）"
        )
    return is_stale, evidence


# ---------------------------------------------------------------------------
# 2026-07-14：/clips/fetch_sync — 同步撥放交集計算（方案 A）
# ---------------------------------------------------------------------------
@app.route("/clips/fetch_sync", methods=["POST"])
def clips_fetch_sync():
    """2 台以上 cam 同時撥放：算交集區間 → 回 4 段 multipart/mixed。

    Body: {nvr_id, cameras: [{device_id, name}, ...], t_center: ISO8601,
           target_seconds: int=60}

    流程（2026-07-14 user 決定：±30s = 1 分鐘視窗、不自動擴搜）：
    1. request_window = [t_center - target_seconds/2, t_center + target_seconds/2]
    2. 並行查每台 cam MPD manifest → 拿該 cam 的 [available_start, available_end]
    3. intersection = 全 cam 都有錄影的交集區間
    4. 若 intersection_length < 5s → 回 NO_COMMON_RECORDING JSON
    5. 並行抓 4 個 fmp4（每個從 intersection_start 開始、長度 = intersection_length）
    6. 回 multipart/mixed 串流，每段 video/mp4 + 自己的 metadata header

    Response 格式：
        HTTP/1.1 200 OK
        Content-Type: multipart/mixed; boundary="..."

        --boundary
        X-Slot-Id: 0
        X-Camera-Id: cam-001
        X-Camera-Name: 大門
        X-Actual-Start: 2026-07-14T08:30:00+00:00
        X-Actual-End: 2026-07-14T08:30:20+00:00
        X-Actual-Duration: 20.00
        Content-Type: video/mp4

        <binary mp4 bytes>
        --boundary
        ...
        --boundary--

    或失敗：
        {error: "NO_COMMON_RECORDING", ...}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import uuid as uuid_mod
    import time as _time
    # 2026-08-06 perf：分階段計時，讓 user 從 server log / browser DevTools
    # Network > Timing 直接看 MPD / probe / fetch / total 各耗時。
    _t_phase = {"mpd": 0.0, "probe": 0.0, "fetch": 0.0}
    _t_total0 = _time.monotonic()

    payload = request.get_json(silent=True) or {}
    try:
        internal_id = int(payload.get("nvr_id", 0))
        cameras = payload.get("cameras") or []
        t_center_iso = payload["t_center"]
        target_seconds = int(payload.get("target_seconds", 60))
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"參數錯誤：{e}"}), 400
    if not internal_id or not cameras or len(cameras) < 1:
        return jsonify({"error": "缺少 nvr_id 或 cameras"}), 400

    t_center = datetime.fromisoformat(t_center_iso.replace("Z", "+00:00"))
    half_window = timedelta(seconds=target_seconds / 2)
    request_start = t_center - half_window
    request_end = t_center + half_window

    nvr_row = webdb.get_nvr(_get_db_path(), internal_id)
    if not nvr_row:
        return jsonify({"error": f"找不到 NVR internal_id={internal_id}"}), 404

    # Auth
    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock":
        session_token = "MOCK-SESSION"
    else:
        try:
            session_token = get_session_for_nvr(internal_id, app.config["SESSION_STORE"])
        except Exception as e:
            logger.error("login 失敗：%s", e)
            return jsonify({"error": f"login 失敗：{e}"}), 502

    client = get_client_for_nvr(nvr_row, session_token=session_token)
    is_mock = os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock"

    # === Step 1: 並行查每台 cam MPD 拿 available range ===
    def query_cam_availability(cam_spec):
        cam_id = cam_spec.get("device_id", "")
        cam_name = cam_spec.get("name", cam_id)
        try:
            dur = client.get_recording_duration(cam_id, request_start)
            logger.info(
                "[fetch_sync] MPD query: cam=%s (%s) t_start=%s dur=%.2fs",
                cam_name, cam_id, request_start.isoformat(), dur,
            )
            return {
                "camera_id": cam_id,
                "name": cam_name,
                "available_start": request_start,
                "available_end": request_start + timedelta(seconds=dur) if dur > 0 else request_start,
                "duration": dur,
                "error": None,
                "auth_failed": False,
            }
        except NvrAuthError as e:
            # 2026-08-06 修：stale session 識別 → 標記 auth_failed=True，
            # 讓 caller 偵測後 invalidate session 並 retry 一次。
            logger.warning(
                "[fetch_sync] MPD query AUTH_FAILED（stale session）: cam=%s (%s) err=%s",
                cam_name, cam_id, e,
            )
            return {
                "camera_id": cam_id,
                "name": cam_name,
                "available_start": request_start,
                "available_end": request_start,
                "duration": 0,
                "error": str(e),
                "auth_failed": True,
            }
        except Exception as e:
            logger.warning(
                "[fetch_sync] MPD query FAILED: cam=%s (%s) t_start=%s err=%s",
                cam_name, cam_id, request_start.isoformat(), e,
            )
            return {
                "camera_id": cam_id,
                "name": cam_name,
                "available_start": request_start,
                "available_end": request_start,
                "duration": 0,
                "error": str(e),
                "auth_failed": False,
            }

    with ThreadPoolExecutor(max_workers=min(8, len(cameras))) as ex:
        _t_mpd_start = _time.monotonic()
        cam_results = list(ex.map(query_cam_availability, cameras))
    _t_phase["mpd"] = _time.monotonic() - _t_mpd_start

    # === Step 2: 算交集（intersection of all available ranges）===
    # 排除查詢失敗（duration=0）的 cam — 它們不算交集
    active_cams = [c for c in cam_results if c["duration"] > 0]
    logger.info(
        "[fetch_sync] MPD done: %.2fs cams=%d active=%d",
        _t_phase["mpd"], len(cameras), len(active_cams),
    )
    if not active_cams:
        # 2026-08-06 修：若所有 cam 都因 stale session（auth_failed=True）失敗，
        # 主動 invalidate SESSION_STORE + 重新登入 + 重試一次，避免 user 卡死需手動重啟 8555。
        if cam_results and all(c.get("auth_failed") for c in cam_results):
            logger.warning(
                "[fetch_sync] All cams stale-session（auth_failed），invalidate cache + retry"
            )
            app.config["SESSION_STORE"].clear(internal_id)
            try:
                session_token = get_session_for_nvr(
                    internal_id, app.config["SESSION_STORE"],
                )
            except Exception as e:
                logger.error("[fetch_sync] retry login 失敗：%s", e)
                return jsonify({"error": f"retry login 失敗：{e}"}), 502
            client = get_client_for_nvr(nvr_row, session_token=session_token)

            def query_cam_availability_fresh(cam_spec):
                cam_id = cam_spec.get("device_id", "")
                cam_name = cam_spec.get("name", cam_id)
                try:
                    dur = client.get_recording_duration(cam_id, request_start)
                    return {
                        "camera_id": cam_id, "name": cam_name,
                        "available_start": request_start,
                        "available_end": request_start + timedelta(seconds=dur) if dur > 0 else request_start,
                        "duration": dur, "error": None, "auth_failed": False,
                    }
                except NvrAuthError as e:
                    logger.warning(
                        "[fetch_sync] MPD query AUTH_FAILED（retry 仍失敗）: cam=%s err=%s",
                        cam_name, e,
                    )
                    return {
                        "camera_id": cam_id, "name": cam_name,
                        "available_start": request_start, "available_end": request_start,
                        "duration": 0, "error": str(e), "auth_failed": True,
                    }
                except Exception as e:
                    logger.warning(
                        "[fetch_sync] MPD query FAILED: cam=%s err=%s",
                        cam_name, e,
                    )
                    return {
                        "camera_id": cam_id, "name": cam_name,
                        "available_start": request_start, "available_end": request_start,
                        "duration": 0, "error": str(e), "auth_failed": False,
                    }

            with ThreadPoolExecutor(max_workers=min(8, len(cameras))) as ex2:
                cam_results = list(ex2.map(query_cam_availability_fresh, cameras))
            active_cams = [c for c in cam_results if c["duration"] > 0]
        if not active_cams:
            return jsonify({
                "error": "NO_COMMON_RECORDING",
                "message": "無可用的錄影時段（所有 cam 都查無資料）",
                "stage": "no_cam_available",
                "intersection_start": request_start.isoformat(),
                "intersection_end": request_end.isoformat(),
            }), 404

    # === Step 2.5：NVR stale cache 探測（2026-07-15 user bug） ===
    # NVR Media API 對某些 cam 在多個相鄰時段會回傳完全相同的 mp4 bytes
    # （cache 把請求 seek 到同一段 mp4；MPD 仍正常報 dur）。
    # 對 user 來說 server 不該把 stale bytes 當合法回應轉送出去。
    # 探測：對每台 active_cam 在 t_center±60s 各 query 一次 fmp4 head bytes，
    # 若 anchor 全回同 md5 → 視為 stale，從 active_cams 排除、記 error。
    # Failure-tolerance：探測失敗（network / NVR fault）→ 視為 not stale（保守）。
    stale_cam_ids: set[str] = set()
    # Stale probe 開關：mock 模式跟 test client 一律 skip（避免 mock mp4 都一樣被誤判）
    skip_stale_probe = is_mock or getattr(client, "disable_stale_probe", False)
    # 2026-08-06 perf critical：probe 預設關掉。
    # 原因：3 anchor × 2 cam × NVR latency 在用戶環境常卡 30s+（040/041.PNG、self-test 90s+）
    # 而 spec F 1 年只發生 1 次 stale（無法證明值得每 request 多付 12 requests）。
    # 重新啟用：clips.html 加 query param ?probe=1，或直接改此 default。
    if not skip_stale_probe:
        _probe_enabled = str(payload.get("probe", "")).lower() in ("1", "true", "yes")
        if not _probe_enabled:
            skip_stale_probe = True
            logger.info(
                "[fetch_sync] probe opt-out (payload.probe=%r), skip",
                payload.get("probe"),
            )
    # 2026-08-06 perf: trust TTL skip probe
    if not skip_stale_probe:
        import time as _time_trust
        _trust_dict = app.config.setdefault("NO_STALE_TRUST", {})
        _trust_expire = _trust_dict.get(internal_id, 0)
        if _trust_expire > _time_trust.time():
            skip_stale_probe = True
            logger.info(
                "[fetch_sync] NVR %d trust TTL 內剩 %.0fs, skip probe",
                internal_id, _trust_expire - _time_trust.time(),
            )
    if not skip_stale_probe:
        _t_probe_start = _time.monotonic()
        from concurrent.futures import ThreadPoolExecutor as _TPE
        def _run_probe(cam_info):
            try:
                is_stale, evidence = _probe_nvr_stale_cache(
                    client, cam_info["camera_id"], request_start,
                )
                return cam_info["camera_id"], is_stale, evidence
            except Exception as e:
                logger.warning(
                    "[fetch_sync] stale probe exception: cam=%s err=%s",
                    cam_info["camera_id"], e,
                )
                return cam_info["camera_id"], False, []
        with _TPE(max_workers=min(8, len(active_cams))) as ex:
            for cid, is_stale, evidence in ex.map(_run_probe, active_cams):
                if is_stale:
                    stale_cam_ids.add(cid)
                    for line in evidence:
                        logger.warning(
                            "[fetch_sync] stale probe cam=%s: %s",
                            cid, line,
                        )
                    logger.warning(
                        "[fetch_sync] NVR_STALE_CACHE: cam=%s (%s) "
                        "被識別為 stale（多時段回相同 bytes），從 active_cams 排除",
                        cid, next(
                            (c["name"] for c in active_cams if c["camera_id"] == cid),
                            cid,
                        ),
                    )
        # 2026-08-06 perf：probe 跑完若**沒有** stale → trust 此 NVR，
        # 下次 fetch_sync 直接 skip probe（節省 12 個 NVR requests）。
        # 若 probe 過程中出 exception（網路問題），保留舊 trust（保守）。
        if not stale_cam_ids:
            _trust_dict = app.config.setdefault("NO_STALE_TRUST", {})
            _trust_dict[internal_id] = _time_trust.time() + _NO_STALE_TRUST_TTL
            logger.info(
                "[fetch_sync] NVR %d probe clean, trust %.0fs",
                internal_id, _NO_STALE_TRUST_TTL,
            )
    if stale_cam_ids:
        # 從 active_cams 移除、記 error
        # ... (existing code)
        excluded = []
        for c in active_cams:
            if c["camera_id"] in stale_cam_ids:
                c["duration"] = 0
                c["error"] = "NVR_STALE_CACHE"
                excluded.append({
                    "camera_id": c["camera_id"],
                    "name": c["name"],
                    "reason": "NVR_STALE_CACHE",  # 純 ASCII（HTTP header latin-1 限制）
                                               # 詳細原因見 server log「NVR_STALE_CACHE」警告行
                })
        active_cams = [c for c in active_cams if c["duration"] > 0]
        if not active_cams:
            return jsonify({
                "error": "NO_COMMON_RECORDING",
                "message": "所有 cam 的 NVR fmp4 都回 stale bytes（多時段相同），可能是 NVR Media API 異常",
                "stage": "all_cams_stale",
                "intersection_start": request_start.isoformat(),
                "intersection_end": request_end.isoformat(),
                "excluded_cams": excluded,
            }), 502
        logger.warning(
            "[fetch_sync] %d 台 cam 被識別為 stale，剩餘 %d 台可用",
            len(stale_cam_ids), len(active_cams),
        )

    # intersection range：start 固定，length 用 target_seconds（不再用 min(MPD durations)）
    #
    # 2026-07-14 user 回報「影片仍只有幾秒」bug 修復：
    # NVR MPD 的 `mediaPresentationDuration` 是「單一 Period」長度（~14 秒，跟 Period 切齊），
    # 不是該 cam 真正的總錄影長度。本來用 min(cam_durations) 算交集會把結果限縮到 14 秒。
    # 改用 target_seconds（使用者選的視窗）作為 intersection 上限：
    #   - MPD duration > 0 只用來判定「有錄影」
    #   - fmp4 stream + _truncate_fmp4_chunks 會按實際 decode time 截
    #   - 若某 cam 真在某秒斷掉，截到該秒；cam 可播更長就播 target_seconds
    intersection_start = request_start
    intersection_length = float(target_seconds)  # ← 用 target_seconds，不被 MPD 限制
    intersection_end = request_start + timedelta(seconds=intersection_length)

    logger.info(
        "[fetch_sync] intersection: start=%s end=%s length=%.1fs (active_cams=%d)",
        intersection_start.isoformat(), intersection_end.isoformat(),
        intersection_length, len(active_cams),
    )

    # === Step 3: 太短直接 NO_COMMON_RECORDING（user 決定：不自動擴搜）===
    # target_seconds 通常 >= 60，實際上幾乎不會觸發；保留以防 client 傳奇怪值。
    if intersection_length < 5.0:
        # 列出每台 cam 的可用範圍讓 user debug
        cam_ranges = [
            {
                "camera_id": c["camera_id"],
                "name": c["name"],
                "available_start": c["available_start"].isoformat(),
                "available_end": c["available_end"].isoformat(),
                "duration_sec": c["duration"],
                "error": c["error"],
            }
            for c in cam_results
        ]
        return jsonify({
            "error": "NO_COMMON_RECORDING",
            "message": f"無共同錄影時段（交集只 {intersection_length:.1f} 秒，建議換時段）",
            "stage": "intersection_too_short",
            "intersection_length_sec": intersection_length,
            "intersection_start": intersection_start.isoformat(),
            "intersection_end": intersection_end.isoformat(),
            "cam_ranges": cam_ranges,
        }), 404

    # === Step 4: 並行抓 4 個 fmp4（從 intersection_start 開始）===
    # 為保留『同步長度 = 交集長度』，每段 clip 在 client 端會被限制播到交集長度。
    # 因為 fmp4 是 stream 沒 end param，server 用實際已 fetch 的 bytes 數判斷
    # 「這段截到什麼時候」；交集長度小於 cam_i duration 時，
    # client 收到超過交集的 bytes 就 ignore（用 video.currentTime 限制）。
    boundary = f"----NVRCLIPSYNC{uuid_mod.uuid4().hex[:12]}"

    def fetch_one_cam(cam_info):
        """回傳 (slot_idx, metadata, mp4_bytes/error) tuple。"""
        slot_idx = cam_info["slot_idx"]
        cam_id = cam_info["camera_id"]
        cam_name = cam_info["name"]
        # 因為這台 cam 被 query 過且 active，所以 fetch 應該成功
        # 但若 NVR 在 query 跟 fetch 之間狀態變，仍可能失敗
        # 2026-07-14 user 回報「2 台就壞」bug：缺 end_time 第三個參數。
        # fetch_clip API 簽名要 (camera_id, start_time, end_time) 三個；
        # 雖然實際 NVR fmp4 endpoint 只用 start_time，但 Protocol 強制要 end_time
        # 為對齊未來可能加「server 截到 end_time 為止」的控制參數，這裡傳完整。
        #
        # 2026-07-14 followup bug：就算算交集給 metadata，前端 <video>.duration
        # 仍是各 cam mp4 自身長度（fmp4 是 stream 沒 end param）。必須傳
        # target_seconds=intersection_length，讓 server 用 mp4 box parser
        # 在「cumulative decode time ≥ target」的第一個 moof 邊界切掉，讓所有
        # cam 拿到的 mp4 都是「交集長度」。
        try:
            body = b"".join(
                client.fetch_clip(
                    cam_id, intersection_start, intersection_end,
                    target_seconds=intersection_length,
                    max_wall_seconds=_MAX_FETCH_WALL_SECONDS,
                )
            )
            if not body:
                logger.warning(
                    "[fetch_sync] fetch EMPTY: cam=%s (%s) start=%s target=%.1fs",
                    cam_name, cam_id, intersection_start.isoformat(), intersection_length,
                )
                return (slot_idx, {
                    "slot_id": slot_idx,
                    "camera_id": cam_id,
                    "name": cam_name,
                    "error": "EMPTY_CLIP",
                }, None)
            logger.info(
                "[fetch_sync] fetch OK: cam=%s (%s) start=%s target=%.1fs bytes=%d",
                cam_name, cam_id, intersection_start.isoformat(),
                intersection_length, len(body),
            )
            return (slot_idx, {
                "slot_id": slot_idx,
                "camera_id": cam_id,
                "name": cam_name,
                "actual_start": intersection_start.isoformat(),
                "actual_end": intersection_end.isoformat(),
                "actual_duration": f"{intersection_length:.2f}",
                "intersection_truncated": cam_info["duration"] > intersection_length,
                "cam_available_duration": f"{cam_info['duration']:.2f}",
            }, body)
        except Exception as e:
            return (slot_idx, {
                "slot_id": slot_idx,
                "camera_id": cam_id,
                "name": cam_name,
                "error": str(e),
            }, None)

    # 修飾 active_cams 給 fetch（2026-07-15 fix：原本用 cam_results，但 stale probe
    # 排除過的 cam 仍會被 fetch，造成 fetch 對被排除 cam 又 fetch 一次）
    if not skip_stale_probe:
        _t_phase["probe"] = _time.monotonic() - _t_probe_start
        logger.info(
            "[fetch_sync] probe done: %.2fs",
            _t_phase["probe"],
        )

    _t_fetch_start = _time.monotonic()
    cam_with_idx = [
        {**r, "slot_idx": i} for i, r in enumerate(active_cams)
    ]
    with ThreadPoolExecutor(max_workers=min(8, len(cam_with_idx))) as ex:
        futures = [ex.submit(fetch_one_cam, c) for c in cam_with_idx]
        fetch_results = [f.result() for f in futures]
    _t_phase["fetch"] = _time.monotonic() - _t_fetch_start
    logger.info(
        "[fetch_sync] fetch done: %.2fs cams=%d active=%d",
        _t_phase["fetch"], len(cameras), len(active_cams),
    )
    # === Step 5: 串成 multipart response ===
    # 統一格式：每段都是 video/mp4 Content-Type，metadata 全在 X-* headers。
    # 失敗的 cam 用 0 bytes body + X-Slot-Error header（ASCII 字串）。
    # 這樣前端 parser 只要追蹤 boundary + 讀 X-* headers，不用處理 Content-Type 切換。
    _t_total = _time.monotonic() - _t_total0
    logger.info(
        "[fetch_sync timing] total=%.2fs mpd=%.2fs probe=%.2fs fetch=%.2fs cams=%d stale=%d",
        _t_total, _t_phase["mpd"], _t_phase["probe"], _t_phase["fetch"],
        len(cameras), len(stale_cam_ids),
    )
    # 摘要放在一個副作用：g object 上掛 X-Server-Timing header（user 可瀏覽 DevTools 看）

    def generate_multipart():
        crlf = b"\r\n"
        for slot_idx, meta, body in fetch_results:
            # boundary line
            yield b"--" + boundary.encode("ascii") + crlf
            yield b"X-Slot-Id: " + str(slot_idx).encode("ascii") + crlf
            yield b"X-Camera-Id: " + meta["camera_id"].encode("utf-8") + crlf
            yield b"X-Camera-Name: " + meta.get("name", meta["camera_id"]).encode("utf-8") + crlf
            if "error" in meta and meta["error"]:
                # 失敗 cam：0-byte body + error header
                yield b"X-Slot-Error: " + meta["error"][:200].encode("utf-8") + crlf
                yield b"Content-Length: 0" + crlf
                yield b"Content-Type: video/mp4" + crlf
                yield crlf  # headers 結束空行
                # (no body)
                yield crlf  # 段結尾 CRLF
            else:
                # 成功 cam：full body + metadata headers
                yield b"X-Actual-Start: " + meta["actual_start"].encode("ascii") + crlf
                yield b"X-Actual-End: " + meta["actual_end"].encode("ascii") + crlf
                yield b"X-Actual-Duration: " + meta["actual_duration"].encode("ascii") + crlf
                yield b"X-Cam-Available-Duration: " + meta.get("cam_available_duration", "0").encode("ascii") + crlf
                yield b"Content-Type: video/mp4" + crlf
                yield crlf  # headers 結束空行
                yield body
                yield crlf  # 段結尾 CRLF
        # end boundary
        yield b"--" + boundary.encode("ascii") + b"--" + crlf

    return Response(
        generate_multipart(),
        mimetype=f"multipart/mixed; boundary={boundary}",
        headers={
            "X-Intersection-Start": intersection_start.isoformat(),
            "X-Intersection-End": intersection_end.isoformat(),
            "X-Intersection-Length": f"{intersection_length:.2f}",
            "X-Cam-Count": str(len(cameras)),
            # 2026-07-15：被 stale probe 排除的 cam 列表，給前端用
            # 格式：device_id|reason 字串（多台以逗號分隔；reason 內逗號換分號）
            "X-Excluded-Cams": _format_excluded_cams(stale_cam_ids, list(cam_results)),
            # 2026-08-06 perf：分階段時序 (W3C Server-Timing 格式 + 自定單位)
            # 例：X-Server-Timing: mpd;dur=120.5, probe;dur=1850.0, fetch;dur=3200.0, total;dur=5170.5
            "X-Server-Timing": ", ".join(
                f"{phase};dur={t * 1000:.1f}"
                for phase, t in _t_phase.items()
            ) + f", total;dur={_t_total * 1000:.1f}",
        },
    )


def _format_excluded_cams(stale_ids: set[str], cam_results: list[dict]) -> str:
    r"""把 stale 的 cam 格式化成 header-friendly string。

    2026-07-15 修正：HTTP header 強制 latin-1，所有欄位必須 ASCII safe。
    用 ensure_ascii=True 把任何非 ASCII 字元轉成 6 字元 escape sequence，
    前端 JSON.parse 再 decode 也沒問題。
    """
    import json as _json
    payload = []
    for c in cam_results:
        if c["camera_id"] in stale_ids:
            payload.append({
                "device_id": c["camera_id"],
                "name": c.get("name", c["camera_id"]),
                "reason": c.get("error", "NVR_STALE_CACHE"),
            })
    return _json.dumps(payload, ensure_ascii=True)


# ---------------------------------------------------------------------------
# 啟動
# ---------------------------------------------------------------------------
def main() -> None:
    host = os.environ.get("NVR_CLIPS_HOST", "0.0.0.0")
    port = int(os.environ.get("NVR_CLIPS_PORT", "8555"))
    debug = os.environ.get("NVR_CLIPS_DEBUG", "").lower() in ("1", "true")
    logger.info("Starting NVR Clip Web UI at http://%s:%d", host, port)
    logger.info("DB: %s", _get_db_path())
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
