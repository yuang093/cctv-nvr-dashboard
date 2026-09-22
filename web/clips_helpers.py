"""Week 6 #018 — clips_app 共用 helpers（8555）。

從原 web/clips_app.py 抽出（Stage A）：
- _SessionStore                in-memory NVR session token cache
- _build_nvr_config            webdb row → AvigilonScanner nvr_config dict
- _login_nvr                   對單台 NVR 登入拿 session token
- get_client_for_nvr           依 env var 決定 Mock 或 Mpd client
- get_session_for_nvr          session token cache（過期或缺 → 重新 login）
- fetch_snapshots_parallel     並行抓 N 台 cam snapshot（轉 base64 jpeg）
- _fetch_snapshot_with_meta    單台抓取 helper
- _probe_nvr_stale_cache       探測 NVR Media API 是否在多時段回相同 bytes（stale）
- _format_excluded_cams        X-Excluded-Cams header 序列化（HTTP header latin-1 安全）

仍從本檔內 import 既有 _MAX_FETCH_WALL_SECONDS env 覆寫邏輯（搬進 factory 時再議）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import cast

from web import db as webdb
from web.snapshot import compress_to_thumbnail as _compress_to_thumbnail

# === Module logger ===
import logging

logger = logging.getLogger("nvr.clips")


# === fmp4 wall-time cap（30s default；env NVR_MAX_FETCH_WALL_SECONDS 可覆寫） ===
_MAX_FETCH_WALL_SECONDS = 30.0
if os.environ.get("NVR_MAX_FETCH_WALL_SECONDS"):
    try:
        _MAX_FETCH_WALL_SECONDS = float(os.environ["NVR_MAX_FETCH_WALL_SECONDS"])
    except ValueError:
        pass


# === DB PATH helper（clip app 專用） ===
def get_db_path() -> str:
    """從 env 讀 NVR_DB_PATH 或 fallback 預設。"""
    return os.environ.get("NVR_DB_PATH", "./nvr_scan.db")


# === NVR session token cache（per-NVR、TTL 30 分鐘、thread-safe） ===
class SessionStore:
    """Avigilon session token 快取（in-memory）。Key = NVR 內部 id（int）。

    2026-08-06 fix11.txt：fmp4 stream wall time cap. NVR 卡死不吐 bytes 時，
    token cache 確保跨 request 重用避免每次 login。
    """

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


# 給既有 from web.clips_app import _SessionStore 兼容符號
_SessionStore = SessionStore


# === NVR row → AvigilonScanner nvr_config dict ===
def build_nvr_config(nvr_row: dict) -> dict:
    return {
        "id": nvr_row["nvr_id"],
        "name": nvr_row["name"],
        "host": nvr_row["host"],
        "port": nvr_row.get("port", 8443),
        "username": nvr_row.get("username"),
        "password": nvr_row.get("password"),
        "verify_ssl": bool(nvr_row.get("verify_ssl", 0)),
    }


# 給既有 from web.clips_app import _build_nvr_config 兼容符號
_build_nvr_config = build_nvr_config


# === Login 與 client factory ===
def login_nvr(nvr_row: dict) -> str:
    """用 AvigilonScanner.login() 拿 session token。失敗拋例外。

    需要 .env 內已設 AVIGILON_USER_NONCE / AVIGILON_USER_KEY（整合帳號）。
    """
    from nvr_scanner import AvigilonScanner, get_credential

    user_nonce = get_credential(
        "AVIGILON_USER_NONCE", "AVIGILON_USER_NONCE", hide=False
    )
    user_key = get_credential("AVIGILON_USER_KEY", "AVIGILON_USER_KEY", hide=True)

    nvr_config = build_nvr_config(nvr_row)
    scanner = AvigilonScanner(
        nvr_config,
        user_nonce=user_nonce,
        user_key=user_key,
        verify_ssl=bool(nvr_row.get("verify_ssl", 0)),
    )
    return scanner.login()


# 給既有 from web.clips_app import _login_nvr 兼容符號
_login_nvr = login_nvr


def get_client_for_nvr(
    nvr_row: dict, *, session_token: str | None = None
):
    """依環境變數決定回 Mock 或 Mpd 客戶端。

    Args:
        nvr_row: webdb.get_nvr(...) 回傳的 dict
        session_token: 已登入的 token；若 None 會自己 login
    """
    from web.clip_retrieval import MockMediaClient, MpdMediaClient

    use_mock = os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock"
    if use_mock:
        return MockMediaClient()

    return MpdMediaClient(
        host=nvr_row["host"],
        port=nvr_row.get("port", 8443),
        session=session_token or "",
        verify_ssl=bool(nvr_row.get("verify_ssl", 0)),
    )


def get_session_for_nvr(internal_id: int, session_store) -> str:
    """從 session_store 拿 token；過期或缺則重 login。"""
    token = session_store.get(internal_id)
    if token:
        return cast(str, token)
    nvr_row = webdb.get_nvr(get_db_path(), internal_id)
    if not nvr_row:
        raise ValueError(f"找不到 NVR internal_id={internal_id}")
    token = login_nvr(nvr_row)
    session_store.set(internal_id, token)
    return token


# === 並行 snapshot 抓取 + Pillow 縮圖 ===
def fetch_snapshot_with_meta(
    client, camera_id: str, at_time: datetime
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


# 兼容既有 from web.clips_app import _fetch_snapshot_with_meta
_fetch_snapshot_with_meta = fetch_snapshot_with_meta


def fetch_snapshots_parallel(
    client,
    camera_ids: list[str],
    at_time: datetime,
    *,
    max_workers: int = 8,
) -> list[dict]:
    """多台相機並行抓 snapshot。回傳 list（順序對齊 camera_ids）。"""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(fetch_snapshot_with_meta, client, cid, at_time): cid
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


# === NVR stale cache 探測（2026-07-15 user bug） ===
def probe_nvr_stale_cache(
    client,
    camera_id: str,
    t_center,
    anchor_offsets: tuple[int, ...] = (-60, 0, 60),
    head_bytes: int = 8 * 1024,
) -> tuple[bool, list[str]]:
    """檢查 NVR Media API 是否對同一 cam 在多個相鄰時段回傳完全相同的 mp4 bytes。

    2026-07-15 user bug：cam 1 在今日下午多個時段的 fmp4 response bytes md5
    完全一樣，但每個時段的 MPD 都回「有錄影」。這是 NVR 內部 cache 把請求
    seek 到同一段 mp4。

    探測法：在 t_center ±N 秒各 query 一段 fmp4（每段只 head head_bytes
    算 partial md5）；若 anchor_offsets 內所有 anchor 的 partial md5
    完全一樣 → 視為 stale。

    Returns:
        (is_stale, evidence_lines)
    """
    evidence: list[str] = []
    md5_by_anchor: dict[int, str] = {}
    error_anchors: list[int] = []

    for off in anchor_offsets:
        probe_t = t_center + timedelta(seconds=off)
        try:
            dur = client.get_recording_duration(camera_id, probe_t)
        except Exception as e:
            error_anchors.append(off)
            evidence.append(
                f"anchor {off:+d}s: MPD query error: {type(e).__name__}: {e}"
            )
            continue
        if dur <= 0.5:
            error_anchors.append(off)
            evidence.append(f"anchor {off:+d}s: dur={dur:.2f}s (<= 0.5, 視為無錄影)")
            continue
        try:
            head = b""
            for chunk in client.fetch_clip(
                camera_id,
                probe_t,
                probe_t + timedelta(seconds=1),
                target_seconds=1,
            ):
                head += chunk
                if len(head) >= head_bytes:
                    break
        except Exception as e:
            error_anchors.append(off)
            evidence.append(
                f"anchor {off:+d}s: fetch_clip error: {type(e).__name__}: {e}"
            )
            continue
        head = head[:head_bytes]
        m = hashlib.md5(head).hexdigest()[:10]
        md5_by_anchor[off] = m
        evidence.append(f"anchor {off:+d}s: dur={dur:.2f}s bytes={len(head)} md5={m}")
    if len(md5_by_anchor) < 2:
        return False, evidence
    distinct = set(md5_by_anchor.values())
    is_stale = len(distinct) == 1
    if is_stale:
        evidence.append(
            f"==> STALE: {len(md5_by_anchor)} anchors 全回同一 bytes（md5={next(iter(distinct))}）"
        )
    return is_stale, evidence


# 兼容舊符號
_probe_nvr_stale_cache = probe_nvr_stale_cache


def format_excluded_cams(stale_ids, cam_results: list[dict]) -> str:
    """把 stale 的 cam 格式化成 header-friendly string。

    2026-07-15 修正：HTTP header 強制 latin-1，所有欄位必須 ASCII safe。
    用 ensure_ascii=True 把任何非 ASCII 字元轉成 6 字元 escape sequence，
    前端 JSON.parse 再 decode 也沒問題。
    """
    payload = []
    for c in cam_results:
        if c["camera_id"] in stale_ids:
            payload.append(
                {
                    "device_id": c["camera_id"],
                    "name": c.get("name", c["camera_id"]),
                    "reason": c.get("error", "NVR_STALE_CACHE"),
                }
            )
    return json.dumps(payload, ensure_ascii=True)


# 兼容舊符號
_format_excluded_cams = format_excluded_cams
