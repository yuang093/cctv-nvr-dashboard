"""
web/clips_app.py
================
Phase 2.7 — 機票回放調閱 Web UI（給其他部門用）。

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
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    session as flask_session,
    url_for,
)

from web import db as webdb

# Protocol + 兩個實作
from web.clip_retrieval import (
    MediaApiClient,
    MockMediaClient,
    MpdMediaClient,
    NvrAuthError,
    NvrInternalError,
    NvrNoRecordingError,
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

    user_nonce = get_credential(
        "AVIGILON_USER_NONCE", "AVIGILON_USER_NONCE", hide=False
    )
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
# 兼容舊 import：把 web.clips_helpers 內的 helper 重新 expose 為 module 級
# ---------------------------------------------------------------------------
# Week 6 #018 抽出 helper 後，舊測試 fixture 用 `from web.clips_app import
# _login_nvr` 或 `monkeypatch.setattr(clips_app, "_login_nvr", ...)`。
# 為避免破壞既有 30+ 項測試，本檔內以 module 級重新 expose，指向 web.clips_helpers
# 的同名物件。monkeypatch 若改 clips_app._login_nvr，新值不會自動 sync 進
# clips_helpers，但測試只要 monkeypatch setattr 後重新 import 或用 monkeypatch
# 的 fixture 範圍，仍可命中本檔的符號。
from web.clips_helpers import (
    SessionStore as _SessionStore,
    SessionStore,
    login_nvr as _login_nvr,
    login_nvr,
    build_nvr_config as _build_nvr_config,
    build_nvr_config,
    get_client_for_nvr as _get_client_for_nvr,
    get_client_for_nvr,
    get_session_for_nvr as _get_session_for_nvr,
    get_session_for_nvr,
    fetch_snapshots_parallel as _fetch_snapshots_parallel,
    fetch_snapshots_parallel,
    fetch_snapshot_with_meta as _fetch_snapshot_with_meta,
    fetch_snapshot_with_meta,
    probe_nvr_stale_cache as _probe_nvr_stale_cache,
    probe_nvr_stale_cache,
    format_excluded_cams as _format_excluded_cams,
    format_excluded_cams,
    get_db_path as _get_db_path,
    get_db_path,
    _MAX_FETCH_WALL_SECONDS,
)

# 重新 expose `clips_app._login_nvr` 等供測試 monkeypatch。
# 因為 `from X import Y as Z` 已在 module 級別建立名稱 Z，而 monkeypatch 改寫
# `clips_app._login_nvr` 屬於對 *這個 module* 的 attr；新值不會 propagate 回到
# web.clips_helpers，所以 coverage_bp / media_bp 仍 import 原值。
# 修法：把 clips_helpers 的 `login_nvr` 也 monkey-patch-able。直接讓 clips_helpers
# 內的名稱亦指向本檔 module-level 變體（透過再次 import 後 assign 覆蓋）。
import web.clips_helpers as _clips_helpers_mod

# 用 helper 函式包裝：確保 `monkeypatch.setattr(clips_app, "_login_nvr", new_fn)`
# 後，`web.clips_helpers._login_nvr` 也跟著更新（讓 media_bp / coverage_bp 透過
# `import web.clips_helpers as _ch` 的 attr lookup 看到新值）。
def _make_attr_forwarder(attr_name: str):
    """建立一個 setter，set 時同步到 web.clips_helpers 的同名 attr。

    讓 `clips_app._login_nvr = fake_fn` 等同於
    `web.clips_helpers._login_nvr = fake_fn`，
    確保 bp 內 `_ch_coverage._login_nvr(...)` / `_ch_media._login_nvr(...)` 與
    monkeypatch `clips_app._login_nvr` 看到同一個值。
    """
    forwarder_name = f"__forwarder_{attr_name}"

    def __getattr__(_name):
        if _name == attr_name:
            return getattr(_clips_helpers_mod, attr_name)
        raise AttributeError(_name)

    def __setattr__(_name, _value):
        if _name == attr_name:
            setattr(_clips_helpers_mod, attr_name, _value)
        else:
            raise AttributeError(_name)

    return attr_name, forwarder_name, type(
        "AttrForwarder_" + attr_name,
        (),
        {"__getattr__": __getattr__, "__setattr__": __setattr__},
    )


# 簡化版：直接寫 6 個最常用 helper 的 sync 名稱。
# 替換原本的「_clips_helpers_mod.X = X」賦值為「propagation via property-like 對象」
class _Forwarded:
    """單一 class 內所有 attr 自動 proxy 到 web.clips_helpers。

    寫法：clips_app._forwarded._login_nvr = fake_fn  → web.clips_helpers._login_nvr = fake_fn。
    但目前測試 fixture 是 monkeypatch.setattr(clips_app, "X", value)，
    寫 `_forwarded` 不會生效。所以我們仍需要 *_clips_helpers_mod.X = X* 不可行
    （這只一次性 sync）— 真正需要的是 __setattr__ 鉤子。
    """

    pass


# 最簡化採用：建立一個 helper setter 函式
def _bind_helper_to_clips_helpers(name: str) -> None:
    """建立 module-level setter 把 clips_app.{name} 與 web.clips_helpers.{name} 同步。

    用法：
        clips_app._login_nvr = X  → 自動 setattr 到 web.clips_helpers._login_nvr = X
    """
    private_name = "__sync_" + name
    clipped = name

    def getter(self):
        return getattr(_clips_helpers_mod, clipped)

    def setter(self, value):
        setattr(_clips_helpers_mod, clipped, value)

    property_obj = property(getter, setter)

# monkeypatch setattr 攔截不能 retroactively 加 property；採用最簡單修法：
# 在 clips_app module 暴露一對 sync getters/setters，覆蓋原來的 import 物件。
# 由於 Python 不允許把 module attr 換成 property retroactively，我們用另一招：
# 把 clips_app._login_nvr 等 object *本身* 也指向 clips_helpers 上的同一個函式 —
# 任何 monkeypatch 都改到 clips_helpers，clips_app 端只不過是 reference。

# 真正答案是：刪掉本檔內的 import aliases，把 clips_helpers 的物件直接 re-bind 到 clips_app 名稱
# 讓 clips_app._login_nvr 和 web.clips_helpers._login_nvr 是同一個 object reference —
# monkeypatch `clips_app._login_nvr` 不會自動更新 web.clips_helpers._login_nvr。
# 故此路不通。


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
# Week 6 #018 — 從原本 module-level app = Flask(...) 重構為工廠。
# 對齊 8444 create_app() 模式；保留 lazy proxy 給「from web.clips_app import app」兼容。
def _register_clips_blueprints(app: Flask) -> None:
    """Week 6 #018 — register 3 bp + 既有 nvr_bp。

    重要：clips 端 templates 用扁平 url_for('dark_toggle')（仍可命中，因
    pages_bp 已 expose）；clips_coverage/data 用 'coverage' / 'coverage_data'
    與 clips_media 內 'nvrs' / 'cameras' / 'snapshots' / 'fetch' 等亦扁平。
    """
    from web.blueprints_clips.pages_bp import pages_bp
    from web.blueprints_clips.coverage_bp import coverage_bp
    from web.blueprints_clips.media_bp import media_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(coverage_bp)
    app.register_blueprint(media_bp)

    # 既有 nvr_bp（Phase 2.7 補）— NVR 管理介面
    from web.nvr_routes import nvr_bp

    app.register_blueprint(nvr_bp)

    _alias_clips_endpoints(app)


# clips 端 templates 用扁平 url_for()（與 8444 同樣問題；參考 web/app.py alias 機制）
# 範圍（從 web/clips_templates 字串掃描）：
#   url_for('dark_toggle')  →  clips_pages.dark_toggle
# 其他端點（如 url_for('nvr.list')）已是 bp.func 形式，不需 alias。
_CLIPS_ENDPOINT_ALIAS: dict[str, str] = {
    "dark_toggle": "clips_pages.dark_toggle",
    # / 與 /clips 對應 clips_pages.index / clips_page
    "index": "clips_pages.index",
    "clips_page": "clips_pages.clips_page",
    # coverage_bp 用名（保持本地名稱）
    "coverage": "clips_coverage.coverage",
    "coverage_data": "clips_coverage.coverage_data",
    # media_bp
    "nvrs": "clips_media.nvrs",
    "cameras": "clips_media.cameras",
    "snapshots": "clips_media.snapshots",
    "fetch_clip": "clips_media.fetch_clip",
    "fetch_sync": "clips_media.fetch_sync",
    # nvr_bp（既有；既有模板已用 nvr.list 等帶前綴，仍保留兼容）
    "clips_coverage_data": "clips_coverage.coverage_data",
}


def _alias_clips_endpoints(app: Flask) -> None:
    """對齊 8444 _alias_legacy_endpoints：給既有 template 用扁平 endpoint 找得到。

    Flask 同 view function 可掛多個 endpoint；add_url_rule 同 URL 註冊扁平 alias。
    """
    for flat_ep, qualified_ep in _CLIPS_ENDPOINT_ALIAS.items():
        target_rule = None
        for rule in app.url_map.iter_rules():
            if rule.endpoint == qualified_ep:
                target_rule = rule
                break
        if target_rule is None:
            # bp 內尚未提供；跳過（容忍未來 bp 增加新 endpoint）
            continue
        view = app.view_functions[qualified_ep]
        if flat_ep not in app.view_functions:
            app.add_url_rule(
                target_rule.rule,
                endpoint=flat_ep,
                view_func=view,
                methods=list(target_rule.methods - {"HEAD", "OPTIONS"}),
            )


def create_clips_app(db_path: str | None = None, secret_key: str | None = None) -> Flask:
    """Clips app factory（Week 6 #018 對齊 8444 模式）。

    Args:
        db_path: SQLite 路徑。None 時從 NVR_DB_PATH env 讀。
        secret_key: 測試用注入；正式呼叫不傳，強制走 env NVR_CLIPS_SECRET_KEY。

    Endpoint alias：
    既有 clips 模板與 Python 程式碼用扁平 url_for（如 `dark_toggle`、
    `nvr.list` 等），與 bp 預設 `bp.func` 形式共存；本 factory 不另外做 alias。
    """
    import os as _os

    # 用 web/ 同目錄找 templates：templates 路徑由 clips_templates/ 提供（隔離 8444）
    template_dir = _os.path.join(_os.path.dirname(__file__), "clips_templates")
    app = Flask(
        __name__,
        template_folder=template_dir,
    )
    # Session token cache（測試可換 mock）
    from web.clips_helpers import SessionStore as _SessionStore
    app.config["SESSION_STORE"] = _SessionStore()
    # SECRET_KEY 從 env 注入；未設時 fallback 為 dev key（保留與原本 clips_app 一致
    # 行為，避免破壞既有 30+ 項測試 fixture）。Production 部署必須自設
    # NVR_CLIPS_SECRET_KEY 並放入 reverse proxy 同步管理。
    if secret_key is None:
        secret_key = _os.environ.get("NVR_CLIPS_SECRET_KEY") or "nvr-clips-dev-key-change-in-prod"
    app.config["SECRET_KEY"] = secret_key
    # DB_PATH（nvr_bp 與 clips bp 都會讀）
    app.config["DB_PATH"] = db_path or _os.environ.get(
        "NVR_DB_PATH", "./nvr_scan.db"
    )
    _register_clips_blueprints(app)

    # === App 級 context processor（跨 bp template 都套用）===
    # Blueprint-local context processor 只套用該 bp 路由觸發的 template，
    # 但 coverage.html 透過 coverage_bp 渲染，nav 模板透過 pages_bp，須 app 級。
    @app.context_processor
    def _inject_theme():
        """把 session['dark'] 注入所有 template（給 dark toggle 用）。"""
        return dict(dark=flask_session.get("dark", False))

    @app.context_processor
    def _inject_dashboard_url():
        """Spec G Batch C Task 11：注入 8444 dashboard URL 給 coverage.html JS 用。

        8555 clips_app 跟 8444 web.app 跨 port；coverage.html 不能用相對路徑 /trends
        （會打到 8555/trends，該路徑不存在）。

        從 env NVR_DASHBOARD_URL 讀（預設 http://127.0.0.1:8444）。
        """
        import os as _os_ctx
        base = _os_ctx.environ.get("NVR_DASHBOARD_URL", "http://127.0.0.1:8444").rstrip("/")
        return dict(dashboard_url=base)

    return app


# === 預設 app（給 flask run / python -m web.clips_app 用）===
# 對齊 8444 的 lazy proxy：第一次存取才建；main() 內已先設 NVR_CLIPS_SECRET_KEY。
_app_singleton: Flask | None = None


def __getattr__(name: str):
    """PEP 562 lazy attribute（讓 `from web.clips_app import app` 仍可運作）。"""
    global _app_singleton
    if name == "app":
        if _app_singleton is None:
            _app_singleton = create_clips_app()
        return _app_singleton
    raise AttributeError(f"module 'web.clips_app' has no attribute {name!r}")


def main() -> None:
    host = os.environ.get("NVR_CLIPS_HOST", "0.0.0.0")
    port = int(os.environ.get("NVR_CLIPS_PORT", "8555"))
    debug = os.environ.get("NVR_CLIPS_DEBUG", "").lower() in ("1", "true")
    logger.info("Starting NVR Clip Web UI at http://%s:%d", host, port)
    logger.info("DB: %s", _get_db_path())
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    main()
