"""
web/db.py
==========
Web UI 用的唯讀 DB helpers。

設計：
    - 與 SqliteWriter 共用 schema，但**自己開新連線**（避免和 worker 搶 transaction）
    - 唯讀：只 SELECT，不寫
    - 每個 helper 是一個查詢，回傳 list[dict] 或 dict

使用：
    from web.db import get_recent_runs, get_run_with_events
    runs = get_recent_runs(db_path, limit=5)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    """開一個唯讀連線（PRAGMA query_only 額外保護）。"""
    p = Path(db_path)
    if not p.exists() and db_path != ":memory:":
        raise FileNotFoundError(f"DB 檔不存在：{db_path}")
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL：reader 不會被 writer block（背景 worker + Web UI 並行安全）
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA query_only = ON")
    return conn


def _connect_writable(db_path: str) -> sqlite3.Connection:
    """開一個**可寫**連線（Phase 2.5a 起，CRUD 需要）。

    注意：不設 PRAGMA query_only。呼叫端必須小心（只用在 CRUD 路由）。
    """
    p = Path(db_path)
    if not p.exists() and db_path != ":memory:":
        raise FileNotFoundError(f"DB 檔不存在：{db_path}")
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout（避免與 worker 競爭時 SQLITE_BUSY）
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    # 啟用 FK 約束（與 worker 端一致；schema 有 FK 必須設）
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# === 統計（Dashboard） ===
def get_overall_stats(db_path: str) -> dict:
    """彙總統計：總 NVR / 總 camera / 24h 內 events 數 / 未解事件相機數 / 錄影中 cam 數。

    Phase 2.8（Arisan 磁磚點擊跳轉）：新增 pending_events 欄位。
    Phase 2.8（Arisan 錄影磁磚）：新增 recording_cameras 欄位。
    注意：未含 online_cameras——那需要即時連 NVR，由 web/app.py dashboard route 算。
    """
    conn = _connect(db_path)
    try:
        nvrs = conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0]
        # Phase 2.8（Arisan 補 2026-07-31）：排除 ghost cam（is_ghost=1，
        # 表示 NVR 已不再管理但 scanner 曾撈到）。Dashboard 統計語意是
        # 「仍被管理的攝影機」，不應包含幽靈。
        cams = conn.execute(
            "SELECT COUNT(*) FROM cameras WHERE is_ghost = 0"
        ).fetchone()[0]
        # 24 小時內的 event 數
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        evs_24h = conn.execute(
            "SELECT COUNT(*) FROM events WHERE detected_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        # 未解決（需處理）事件涵蓋的相機數（distinct device_id）
        pending_events = conn.execute(
            "SELECT COUNT(DISTINCT device_id) FROM events WHERE resolved_at IS NULL"
        ).fetchone()[0]
        # 錄影中 cam 數 = 總 cam - 有未解事件的 cam
        recording_cameras = max(cams - pending_events, 0)
        # 最新一次 run
        last_run = conn.execute(
            "SELECT id, status, started_at, finished_at, "
            "abnormal_cameras, total_cameras "
            "FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_nvrs": nvrs,
            "total_cameras": cams,
            "events_24h": evs_24h,
            "pending_events": pending_events,
            "recording_cameras": recording_cameras,
            "last_run": dict(last_run) if last_run else None,
        }
    finally:
        conn.close()


# === scan_runs ===
def get_recent_runs(db_path: str, limit: int = 5) -> list[dict]:
    """最新 N 次 scan_run。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status,
                   total_nvrs, ok_nvrs, failed_nvrs,
                   total_cameras, abnormal_cameras, error_message
            FROM scan_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_paginated_runs(db_path: str, page: int = 1, per_page: int = 20) -> dict:
    """scan_runs 分頁。"""
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status,
                   total_nvrs, ok_nvrs, failed_nvrs,
                   total_cameras, abnormal_cameras
            FROM scan_runs
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
        return {
            "runs": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        conn.close()


def get_run(db_path: str, run_id: int) -> dict | None:
    """單次 scan_run 詳情。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM scan_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        # 解析 duration（如果有 finished_at）
        if result.get("started_at") and result.get("finished_at"):
            try:
                t0 = datetime.fromisoformat(
                    result["started_at"].replace("Z", "+00:00")
                )
                t1 = datetime.fromisoformat(
                    result["finished_at"].replace("Z", "+00:00")
                )
                result["duration_sec"] = (t1 - t0).total_seconds()
            except Exception:
                result["duration_sec"] = None
        return result
    finally:
        conn.close()


def get_run_events(db_path: str, run_id: int) -> list[dict]:
    """單次 run 的所有 events，JOIN cameras 取 camera_name。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.event_topic, e.device_id, c.camera_name,
                   e.event_topics_json, e.occurred_at, e.detected_at,
                   n.nvr_id, n.name AS nvr_name
            FROM events e
            LEFT JOIN cameras c
                ON e.nvr_id = c.nvr_id AND e.device_id = c.device_id
            LEFT JOIN nvr_servers n ON e.nvr_id = n.id
            WHERE e.scan_run_id = ?
            ORDER BY e.id
            """,
            (run_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 解析 event_topics_json
            try:
                d["event_topics"] = json.loads(d.pop("event_topics_json") or "[]")
            except Exception:
                d["event_topics"] = []
            out.append(d)
        return out
    finally:
        conn.close()


def get_run_cameras(db_path: str, run_id: int) -> list[dict]:
    """單次 run 涉及的 cameras（透過 events 取得 device_id 清單去 join）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT c.device_id, c.camera_name, c.last_seen_at,
                   n.nvr_id, n.name AS nvr_name
            FROM events e
            JOIN cameras c
                ON e.nvr_id = c.nvr_id AND e.device_id = c.device_id
            JOIN nvr_servers n ON e.nvr_id = n.id
            WHERE e.scan_run_id = ?
            ORDER BY n.name, c.device_id
            """,
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_nvr_failures_for_run(db_path: str, run_id: int) -> list[dict]:
    """單次 run 中個別 NVR 連線失敗的清單（給 run_detail 頁顯示）。

    每筆 dict：
        - nvr_id, nvr_name: 設定檔 id / 顯示名稱
        - error_type, error_message: 失敗類型與訊息
        - failed_at: 失敗時間（ISO 8601 UTC）
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT nvr_id, nvr_name, error_type, error_message, failed_at
            FROM nvr_failure_log
            WHERE scan_run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_enabled_nvrs(db_path: str) -> list[dict]:
    """從 DB 撈所有 enabled=1 的 NVR，轉成 batch_scan 預期的 dict 格式。

    Phase 2.7+ 起，取代讀 nvr_config.json。
    回傳格式（給 AvigilonScanner 用）：
        [{
            "id": str,           # NVR 的 nvr_id 字串
            "name": str,
            "host": str,
            "port": int,
            "username": str,
            "password": str,
            "verify_ssl": bool,
            "site_id": str | None,
            "tags": list[str],
        }, ...]
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT nvr_id, name, host, port, username, password,
                   verify_ssl, site_id, tags
            FROM nvr_servers
            WHERE enabled = 1
            ORDER BY name
            """,
        ).fetchall()
        out = []
        for r in rows:
            try:
                tags = json.loads(r["tags"] or "[]")
            except (json.JSONDecodeError, TypeError):
                tags = []
            out.append({
                "id": r["nvr_id"],
                "name": r["name"],
                "host": r["host"],
                "port": int(r["port"]),
                "username": r["username"] or "",
                "password": r["password"] or "",
                "verify_ssl": bool(r["verify_ssl"]),
                "site_id": r["site_id"],
                "tags": tags,
            })
        return out
    finally:
        conn.close()


def set_nvr_enabled(db_path: str, internal_id: int, enabled: bool) -> bool:
    """切換單台 NVR 的啟用狀態。回傳是否實際改動（False = 找不到或已是該狀態）。"""
    conn = _connect_writable(db_path)
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute(
            """
            UPDATE nvr_servers
            SET enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if enabled else 0, now_iso, internal_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# === NVRs ===
def get_nvrs(db_path: str) -> list[dict]:
    """所有 NVR 設定（含該 NVR 的 camera 數 + 啟用狀態）。

    注意：v2.7+ 起 `enabled` 欄位由 DB 管理（取代 nvr_config.json 的 enabled 過濾）。
    enabled=1 會被背景掃描；enabled=0 跳過（但仍出現在清單）。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT n.id, n.nvr_id, n.name, n.host, n.port, n.site_id, n.tags,
                   n.verify_ssl, n.enabled, n.updated_at,
                   (SELECT COUNT(*) FROM cameras WHERE nvr_id = n.id) AS camera_count,
                   (SELECT MAX(last_seen_at) FROM cameras WHERE nvr_id = n.id)
                       AS last_seen_at
            FROM nvr_servers n
            ORDER BY n.name
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d["tags"] or "[]")
            except Exception:
                d["tags"] = []
            out.append(d)
        return out
    finally:
        conn.close()


# === NVR CRUD（Phase 2.5a） ===

def get_nvrs_paginated(
    db_path: str,
    *,
    page: int = 1,
    per_page: int = 20,
    q: str | None = None,
) -> dict:
    """NVR 清單分頁 + 搜尋（給 80+ NVR 場景用）。

    Args:
        db_path: SQLite 路徑。
        page: 頁碼（≥1）。
        per_page: 每頁筆數（預設 20）。
        q: 搜尋字串（比對 nvr_id / name / host；LIKE）。

    Returns:
        {
            "nvrs": list[dict],
            "total": int,
            "page": int,
            "per_page": int,
            "total_pages": int,
        }
    """
    conn = _connect(db_path)
    try:
        where = ""
        params: list[Any] = []
        if q:
            # 跳脫 LIKE 萬用字元（% / _ / \），避免使用者輸入造成誤匹配
            safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = "WHERE n.nvr_id LIKE ? ESCAPE '\\' OR n.name LIKE ? ESCAPE '\\' OR n.host LIKE ? ESCAPE '\\'"
            params = [f"%{safe}%", f"%{safe}%", f"%{safe}%"]
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM nvr_servers n {where}", params
        ).fetchone()
        total = total_row[0] if total_row else 0
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT n.id, n.nvr_id, n.name, n.host, n.port, n.site_id, n.tags,
                   n.verify_ssl, n.enabled, n.updated_at,
                   (SELECT COUNT(*) FROM cameras WHERE nvr_id = n.id) AS camera_count
            FROM nvr_servers n
            {where}
            ORDER BY n.name
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d["tags"] or "[]")
            except Exception:
                d["tags"] = []
            out.append(d)
        return {
            "nvrs": out,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        conn.close()


def get_nvr(db_path: str, internal_id: int) -> dict | None:
    """單一 NVR 完整資料（含 password，僅給 edit 表單用）。

    Returns:
        dict（含所有欄位）或 None（找不到）。
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM nvr_servers WHERE id = ?", (internal_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["tags"] = json.loads(d["tags"] or "[]")
        except Exception:
            d["tags"] = []
        return d
    finally:
        conn.close()


# === Phase 2.8（Arisan）Phase #5：/wall 相機牆 ===
# 訊號 / 無訊號 分類用的關鍵字（與 event_kind_catalog 表一致；State 是過渡）
_SIGNAL_LOST_TOPICS = frozenset({
    "DEVICE_VIDEO_SIGNAL_LOST",
    "DEVICE_COMMUNICATION_LOST",
    "DEVICE_CONNECTION_ERROR",
    "DEVICE_TAMPERING",
})
_NO_SIGNAL_TOPICS = frozenset({
    "STATE_LONG_FAILED",
    "STATE_DISCONNECTED",
    "STATE_NOT_RESPONDING",
    "STATE_FAILED",
    "STATE_TIMED_OUT",
    "STATE_NETWORK_DOWN",
    "DEVICE_DISCONNECTED",
    "DEVICE_LONG_FAILED",
    "STATE_AUTH_FAILED",
    "STATE_BAD_CERTIFICATE",
})


def get_wall_cameras(db_path: str, filter_kind: str = "all") -> list[dict]:
    """Phase 2.8（Arisan）Phase #5：相機牆資料（純文字版，無縮圖）。

    從 cameras LEFT JOIN 最新一筆未解 events（per cam）→ 推分類：
        - filter='all'         → 全部
        - filter='online'      → 沒未解事件（視為在線）
        - filter='signal_lost' → 最新未解 topic ∈ SIGNAL_LOST
        - filter='no_signal'   → 最新未解 topic ∈ NO_SIGNAL

    Returns:
        list of {"nvr_id", "nvr_name", "device_id", "camera_name",
                 "ip_address", "latest_topic", "latest_topic_zh", "category"}
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                c.nvr_id,
                n.name AS nvr_name,
                c.device_id,
                c.camera_name,
                c.ip_address,
                (
                    SELECT e.event_topic FROM events e
                    WHERE e.nvr_id = c.nvr_id
                      AND e.device_id = c.device_id
                      AND e.resolved_at IS NULL
                    ORDER BY e.occurred_at DESC, e.id DESC
                    LIMIT 1
                ) AS latest_topic
            FROM cameras c
            JOIN nvr_servers n ON n.id = c.nvr_id
            WHERE c.is_ghost = 0
            ORDER BY n.name, c.camera_name, c.device_id
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        topic = r["latest_topic"]
        if topic in _SIGNAL_LOST_TOPICS:
            cat = "signal_lost"
        elif topic in _NO_SIGNAL_TOPICS:
            cat = "no_signal"
        else:
            cat = "online"
        if filter_kind != "all" and cat != filter_kind:
            continue
        out.append({
            "nvr_id": r["nvr_id"],
            "nvr_name": r["nvr_name"],
            "device_id": r["device_id"],
            "camera_name": r["camera_name"],
            "ip_address": r["ip_address"],
            "latest_topic": topic,
            "latest_topic_zh": get_event_label_zh(db_path, topic) if topic else None,
            "category": cat,
            "recording_pct": get_latest_recording_pct(db_path, r["nvr_id"], r["device_id"]),
        })
    return out


# === 2026-07-29：相機牆視覺化（含縮圖 + 計數） ===

import base64 as _base64  # 給 snapshot_b64 編碼


def get_wall_cameras_with_snapshots(
    db_path: str,
    filter_kind: str = "all",
    nvr_id: int | None = None,
) -> list[dict]:
    """8444 /wall 視覺化用：含 base64 縮圖 + 預設按嚴重度排序。

    與 get_wall_cameras 差異：
      - 多了 snapshot_b64（base64 string；沒快照 → None）
      - 多了 has_snapshot (bool)
      - 多了 latest_event_at（給排序用）
      - 預設排序：訊號中斷 > 無訊號 > 在線（同類內事件新→舊；在線按 cam 名稱）

    Args:
        db_path: SQLite 路徑。
        filter_kind: 'all' | 'online' | 'signal_lost' | 'no_signal'；無效值 fallback 到 all。
        nvr_id: 若指定，只回該 NVR 的 cam（內部 id）；None（預設）→ 全部。

    Returns:
        list of dict（每台 cam 一個）：
            {
              "nvr_id", "nvr_name", "device_id", "camera_name", "ip_address",
              "latest_topic", "latest_topic_zh", "latest_event_at",
              "category", "recording_pct",
              "snapshot_b64", "has_snapshot",
            }
    """
    if filter_kind not in ("all", "online", "signal_lost", "no_signal"):
        filter_kind = "all"

    # nvr_id 篩選（Task 1：給 /fleet 取單一 NVR 的 cam 清單；向後相容：None → 全部）
    # 2026-07-30：預設過濾 is_ghost = 0（NVR 已不再管理的 cam）
    extra_where_parts = ["c.is_ghost = 0"]
    extra_params: list[Any] = []
    if nvr_id is not None:
        extra_where_parts.append("c.nvr_id = ?")
        extra_params.append(nvr_id)
    extra_where = " WHERE " + " AND ".join(extra_where_parts)

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT
                c.nvr_id,
                n.name AS nvr_name,
                c.device_id,
                c.camera_name,
                c.ip_address,
                (
                    SELECT e.event_topic FROM events e
                    WHERE e.nvr_id = c.nvr_id
                      AND e.device_id = c.device_id
                      AND e.resolved_at IS NULL
                    ORDER BY e.occurred_at DESC, e.id DESC
                    LIMIT 1
                ) AS latest_topic,
                (
                    SELECT e.occurred_at FROM events e
                    WHERE e.nvr_id = c.nvr_id
                      AND e.device_id = c.device_id
                      AND e.resolved_at IS NULL
                    ORDER BY e.occurred_at DESC, e.id DESC
                    LIMIT 1
                ) AS latest_event_at,
                snap.jpeg_bytes AS snapshot_bytes,
                snap.captured_at AS snapshot_captured_at
            FROM cameras c
            JOIN nvr_servers n ON n.id = c.nvr_id
            LEFT JOIN camera_snapshots snap
                ON snap.nvr_id = c.nvr_id AND snap.camera_id = c.device_id
            {extra_where}
            """,
            extra_params,
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        topic = r["latest_topic"]
        if topic in _SIGNAL_LOST_TOPICS:
            cat = "signal_lost"
        elif topic in _NO_SIGNAL_TOPICS:
            cat = "no_signal"
        else:
            cat = "online"
        if filter_kind != "all" and cat != filter_kind:
            continue
        snap_bytes = r["snapshot_bytes"]
        out.append({
            "nvr_id": r["nvr_id"],
            "nvr_name": r["nvr_name"],
            "device_id": r["device_id"],
            "camera_name": r["camera_name"],
            "ip_address": r["ip_address"],
            "latest_topic": topic,
            "latest_topic_zh": get_event_label_zh(db_path, topic) if topic else None,
            "latest_event_at": r["latest_event_at"],
            "category": cat,
            "recording_pct": get_latest_recording_pct(db_path, r["nvr_id"], r["device_id"]),
            "snapshot_b64": _base64.b64encode(snap_bytes).decode("ascii") if snap_bytes else None,
            "has_snapshot": bool(snap_bytes),
            "snapshot_captured_at": r["snapshot_captured_at"],
        })

    # 排序：嚴重度 → 事件時間（NULL 排後）→ cam 名稱
    # 邏輯：
    #   1. 分組：嚴重度由高到低（signal_lost > no_signal > online）
    #   2. 同組內：signal_lost / no_signal 按事件時間新→舊；online 按 cam 名稱 A→Z
    _SEVERITY_ORDER = ("signal_lost", "no_signal", "online")
    by_cat: dict[str, list[dict]] = {c: [] for c in _SEVERITY_ORDER}
    for r in out:
        by_cat.setdefault(r["category"], []).append(r)
    final: list[dict] = []
    for cat in _SEVERITY_ORDER:
        group = by_cat.get(cat, [])
        if cat == "online":
            group.sort(key=lambda r: r["camera_name"])
        else:
            # ISO8601 字串字典序 = 時間序；新→舊用 reverse=True
            group.sort(key=lambda r: r["latest_event_at"] or "", reverse=True)
        final.extend(group)
    return final


def get_wall_filter_counts(db_path: str) -> dict[str, int]:
    """永遠回 4 類全 DB 計數（不受 filter_kind 影響）。

    Returns:
        {"all": N, "online": N, "signal_lost": N, "no_signal": N}
    """
    rows = get_wall_cameras_with_snapshots(db_path, filter_kind="all")
    counts = {"all": len(rows), "online": 0, "signal_lost": 0, "no_signal": 0}
    for r in rows:
        counts[r["category"]] += 1
    return counts


def get_latest_recording_pct(db_path: str, nvr_id: int, camera_id: str) -> float | None:
    """查單台 cam 的最近一次 24h 錄影完整率（0.0~1.0）。

    沒有 recording_status 紀錄 → 回 None（UI 顯示「—」）。
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT completeness FROM recording_status "
            "WHERE nvr_id=? AND camera_id=?",
            (nvr_id, camera_id),
        ).fetchone()
    finally:
        conn.close()
    return float(row["completeness"]) if row else None


def get_top_missing_cameras(db_path: str, limit: int = 5) -> list[dict]:
    """Phase 2.8（Arisan 錄影排名）：24h 缺錄最多的 cam 前 N 名。

    從 `recording_status` LEFT JOIN cameras + nvr_servers，按 missing_seconds 降序。

    Returns:
        list of dict: [
            {
                "camera_id" (str),
                "camera_name" (str),
                "nvr_name" (str),
                "missing_seconds" (float),
                "missing_hours" (float),
                "completeness" (float),
                "checked_at" (str, ISO UTC),
            }
        ]，沒資料 → 空 list。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                rs.camera_id,
                COALESCE(c.camera_name, rs.camera_id) AS camera_name,
                n.name AS nvr_name,
                rs.missing_seconds,
                rs.completeness,
                rs.checked_at
            FROM recording_status rs
            LEFT JOIN cameras c
                ON c.nvr_id = rs.nvr_id AND c.device_id = rs.camera_id
            INNER JOIN nvr_servers n
                ON n.id = rs.nvr_id AND n.enabled = 1
            -- Phase 2.8（Arisan 補 2026-07-31）：排除 ghost cam。
            -- 保留 LEFT JOIN：NVR 剛 upsert_recording_status 但 cam row 還沒建
            -- 也合法（NULL 通過）；只要匹配到的 cam 不是 ghost 就保留。
            WHERE c.id IS NULL OR c.is_ghost = 0
            ORDER BY rs.missing_seconds DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        item = dict(r)
        item["missing_hours"] = round(item["missing_seconds"] / 3600.0, 2)
        out.append(item)
    return out


def get_latest_recording_check_at(db_path: str) -> str | None:
    """Phase 2.8 補：回傳 recording_status 表最新的 checked_at（ISO UTC string）。

    沒資料 → None。給 dashboard 顯示「最後更新 X 小時前」用。
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(checked_at) AS latest FROM recording_status"
        ).fetchone()
    finally:
        conn.close()
    return row["latest"] if row and row["latest"] else None


def get_recording_status_for_camera(
    db_path: str, nvr_id: int, camera_id: str
) -> dict | None:
    """查單台 cam 的 recording_status 完整列。

    沒紀錄 → 回 None。否則回：
        {
            "window_start": "...",
            "window_end": "...",
            "completeness": 0.0~1.0,
            "missing_seconds": float,
            "missing_hours": 換算的小時數,
            "checked_at": "...",
        }
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT window_start, window_end, completeness, missing_seconds, checked_at "
            "FROM recording_status WHERE nvr_id=? AND camera_id=?",
            (nvr_id, camera_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    info = dict(row)
    info["missing_hours"] = round(info["missing_seconds"] / 3600.0, 2)
    return info


def list_cameras_for_nvr(db_path: str, internal_id: int) -> list[dict]:
    """某 NVR 底下的 cameras（給 clip Web UI 抓 snapshot 用）。

    Returns:
        list of {"device_id": str, "name": str}
        （device_id = NVR 端的 camera identifier，呼叫 Media API 時用）
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT device_id, camera_name FROM cameras WHERE nvr_id = ? "
            "ORDER BY camera_name, device_id",
            (internal_id,),
        ).fetchall()
        return [{"device_id": r["device_id"], "name": r["camera_name"]} for r in rows]
    finally:
        conn.close()


def _normalize_tags(tags) -> list[str]:
    """tags 可以是 list 或 "a;b;c" 字串 → 統一回傳 list。"""
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(";") if t.strip()]
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return []


def create_nvr(db_path: str, nvr_data: dict) -> int:
    """新增 NVR；回傳內部 id。

    Args:
        nvr_data: 必含 nvr_id / name / host / username / password；
                  port 預設 8443；verify_ssl 預設 0；tags 可 list 或 "a;b"。

    Returns:
        新 NVR 的內部 id（INTEGER）。

    Raises:
        ValueError: nvr_id 重複或其他約束違反。
    """
    conn = _connect_writable(db_path)
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute(
            """
            INSERT INTO nvr_servers (
                nvr_id, name, host, port, username, password,
                verify_ssl, site_id, tags, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nvr_data["nvr_id"],
                nvr_data["name"],
                nvr_data["host"],
                int(nvr_data.get("port", 8443)),
                nvr_data.get("username", ""),
                nvr_data.get("password", ""),
                1 if nvr_data.get("verify_ssl") else 0,
                nvr_data.get("site_id") or None,
                json.dumps(_normalize_tags(nvr_data.get("tags", []))),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ValueError(f"id 重複或違反約束：{e}") from e
    finally:
        conn.close()


def update_nvr(
    db_path: str,
    internal_id: int,
    nvr_data: dict,
    *,
    password_changed: bool,
) -> None:
    """更新 NVR。

    Args:
        internal_id: DB 內部 id（不是 nvr_id 字串）。
        nvr_data: 同 create_nvr（不含 nvr_id；不能改）。
        password_changed: 若 False，password 欄位不寫入（保留原值）。

    Raises:
        ValueError: 找不到或違反約束。
    """
    conn = _connect_writable(db_path)
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if password_changed:
            conn.execute(
                """
                UPDATE nvr_servers SET
                    name = ?, host = ?, port = ?, username = ?, password = ?,
                    verify_ssl = ?, site_id = ?, tags = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    nvr_data["name"],
                    nvr_data["host"],
                    int(nvr_data.get("port", 8443)),
                    nvr_data.get("username", ""),
                    nvr_data.get("password", ""),
                    1 if nvr_data.get("verify_ssl") else 0,
                    nvr_data.get("site_id") or None,
                    json.dumps(_normalize_tags(nvr_data.get("tags", []))),
                    now_iso,
                    internal_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE nvr_servers SET
                    name = ?, host = ?, port = ?, username = ?,
                    verify_ssl = ?, site_id = ?, tags = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    nvr_data["name"],
                    nvr_data["host"],
                    int(nvr_data.get("port", 8443)),
                    nvr_data.get("username", ""),
                    1 if nvr_data.get("verify_ssl") else 0,
                    nvr_data.get("site_id") or None,
                    json.dumps(_normalize_tags(nvr_data.get("tags", []))),
                    now_iso,
                    internal_id,
                ),
            )
        if conn.total_changes == 0:
            conn.rollback()
            raise ValueError(f"找不到 internal_id={internal_id} 的 NVR")
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_nvr(db_path: str, internal_id: int) -> dict:
    """刪除 NVR 與其 cameras；保留 scan_runs / events 歷史。

    events 表的 nvr_id / camera_id 對 nvr_servers / cameras 有 FK 約束。
    為了「保留事件歷史」（events.nvr_id / events.camera_id 變成 orphan
    整數但仍可查詢），本函式在 connection 範圍內暫時關閉 FK 約束。

    範圍僅限本 connection；worker 端 SqliteWriter 用自己的 connection，
    FK 仍正常運作，跨 process 安全。

    Returns:
        {"cameras_deleted": int} 給 UI 顯示訊息用。
    """
    conn = _connect_writable(db_path)
    try:
        # 確保前一個 implicit transaction 已結束，才能切 PRAGMA
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")

        cam_cur = conn.execute(
            "DELETE FROM cameras WHERE nvr_id = ?", (internal_id,)
        )
        cameras_deleted = cam_cur.rowcount

        srv_cur = conn.execute(
            "DELETE FROM nvr_servers WHERE id = ?", (internal_id,)
        )
        if srv_cur.rowcount == 0:
            conn.rollback()
            raise ValueError(f"找不到 internal_id={internal_id} 的 NVR")

        conn.commit()
        return {"cameras_deleted": cameras_deleted}
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


# === Phase 2.6：背景掃描狀態查詢 ===

def get_last_scan_run(db_path: str) -> dict | None:
    """最近一次 scan_run（給背景 scan thread 寫入 run_id 用）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, started_at, finished_at, status,
                   total_nvrs, ok_nvrs, failed_nvrs,
                   total_cameras, abnormal_cameras, error_message
            FROM scan_runs
            ORDER BY id DESC
            LIMIT 1
            """,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# === Phase 2.6：故障攝影機分組總覽（/abnormal 頁用） ===

# 故障類型中文對照（給 UI / PDF 顯示用）
# 涵蓋兩大類：
#   - DEVICE_*：明確的硬體/影像異常（拔網路線、訊號斷、破壞等）
#   - STATE_*：由 connection_state 衍生（連線中、斷線、憑證錯等）
ABNORMAL_TOPIC_ZH: dict[str, str] = {
    # === DEVICE_*（v1 既有的 8 種）===
    "DEVICE_VIDEO_SIGNAL_LOST":  "影像訊號斷線（黑畫面）",
    "DEVICE_TAMPERING":          "破壞/遮蔽（場景改變）",
    "DEVICE_COMMUNICATION_LOST": "通訊中斷",
    "DEVICE_CONNECTION_ERROR":   "連線錯誤",
    "DEVICE_LONG_FAILED":        "長期失敗（拔線）",
    "DEVICE_DISCONNECTED":       "斷線",
    "DEVICE_ANOMALY_START":      "影像分析異常",
    "DEVICE_UNUSUAL_STARTED":    "未預期活動",
    # === STATE_*（由 connection_state 衍生，常見場景）===
    "STATE_DISCONNECTED":        "斷線（攝影機無回應）",
    "STATE_NOT_RESPONDING":      "無回應（攝影機 hang）",
    "STATE_FAILED":              "連線失敗",
    "STATE_LONG_FAILED":         "長期失敗（拔網路線）",
    "STATE_BAD_CERTIFICATE":     "憑證錯誤",
    "STATE_AUTH_FAILED":         "認證失敗（帳密錯）",
    "STATE_NETWORK_DOWN":        "網路斷線",
    "STATE_TIMED_OUT":           "連線逾時",
    "STATE_CONNECTING":          "連線中（短暫狀態）",
}


# 泛用 fallback（沒對照到的 STATE_/DEVICE_ 也給人話）
_GENERIC_TOPIC_ZH: dict[str, str] = {
    # DEVICE_* 通用
    "DEVICE_VIDEO_SIGNAL_LOST":  "影像訊號斷線（黑畫面）",
    "DEVICE_TAMPERING":          "破壞/遮蔽（場景改變）",
    "DEVICE_COMMUNICATION_LOST": "通訊中斷",
    "DEVICE_CONNECTION_ERROR":   "連線錯誤",
    "DEVICE_LONG_FAILED":        "長期失敗（拔線）",
    "DEVICE_DISCONNECTED":       "斷線",
    "DEVICE_ANOMALY_START":      "影像分析異常",
    "DEVICE_UNUSUAL_STARTED":    "未預期活動",
    # STATE_<CONNECTION_STATE> 泛用對照
    "STATE_CONNECTED":           "已連線（正常）",
    "STATE_CONNECTING":          "連線中",
    "STATE_DISCONNECTED":        "斷線（攝影機無回應）",
    "STATE_FAILED":              "連線失敗",
    "STATE_LONG_FAILED":         "長期失敗（拔網路線）",
    "STATE_NOT_RESPONDING":      "無回應（攝影機 hang）",
    "STATE_BAD_CERTIFICATE":     "憑證錯誤",
    "STATE_AUTH_FAILED":         "認證失敗（帳密錯）",
    "STATE_NETWORK_DOWN":        "網路斷線",
    "STATE_TIMED_OUT":           "連線逾時",
    "STATE_UNKNOWN":             "連線狀態不明",
    "STATE_RECONNECTING":        "重新連線中",
    "STATE_UPGRADING":           "韌體升級中",
    "STATE_INCOMPATIBLE":        "不相容",
    "STATE_NOT_SUPPORTED":       "不支援此操作",
}


def get_topic_zh(topic: str) -> str:
    """查中文對照（無對照時 fallback 給人話翻譯）。

    順序：
        1. ABNORMAL_TOPIC_ZH 明確對照（含 DEVICE_/STATE_）
        2. _GENERIC_TOPIC_ZH 泛用對照
        3. STATE_<X> 自動拆字（例 STATE_FOO_BAR → "Foo Bar"）
        4. 原文（最後 fallback）
    """
    if topic in ABNORMAL_TOPIC_ZH:
        return ABNORMAL_TOPIC_ZH[topic]
    if topic in _GENERIC_TOPIC_ZH:
        return _GENERIC_TOPIC_ZH[topic]
    # 自動拆字：STATE_FOO_BAR → "Foo Bar"
    if topic.startswith("STATE_") or topic.startswith("DEVICE_"):
        parts = topic.split("_")[1:]  # 去掉前綴
        return " ".join(p.capitalize() for p in parts if p)
    return topic


def get_abnormal_cameras_grouped(db_path: str) -> list[dict]:
    """目前 OPEN 異常事件的攝影機（group by NVR + device）。

    每組：
        nvr_id, nvr_name, device_id, camera_name,
        topics: list[event_topic]      # 這台相機有哪些故障類型
        first_detected: ISO            # 首次發現
        last_detected:  ISO            # 最新一次
        open_count: int                # 累積幾筆 open 事件

    排序：先 NVR name，再 first_detected 早 → 晚。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.nvr_id, n.name AS nvr_name,
                   e.device_id, c.camera_name,
                   e.event_topic,
                   e.detected_at
            FROM events e
            INNER JOIN cameras c
                ON e.nvr_id = c.nvr_id AND e.device_id = c.device_id
            INNER JOIN nvr_servers n ON e.nvr_id = n.id
            WHERE e.resolved_at IS NULL
            ORDER BY n.name, e.detected_at
            """,
        ).fetchall()
    finally:
        conn.close()

    # Python 端 group
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["nvr_id"], r["device_id"])
        if key not in groups:
            groups[key] = {
                "nvr_id": r["nvr_id"],
                "nvr_name": r["nvr_name"],
                "device_id": r["device_id"],
                "camera_name": r["camera_name"] or f"(unknown #{r['device_id']})",
                "topics": [],
                "first_detected": r["detected_at"],
                "last_detected": r["detected_at"],
                "open_count": 0,
            }
        g = groups[key]
        if r["event_topic"] not in g["topics"]:
            g["topics"].append(r["event_topic"])
        g["open_count"] += 1
        if r["detected_at"] < g["first_detected"]:
            g["first_detected"] = r["detected_at"]
        if r["detected_at"] > g["last_detected"]:
            g["last_detected"] = r["detected_at"]
    # 排序：先 NVR name，再首次發現（早→晚）
    return sorted(
        groups.values(),
        key=lambda g: (g["nvr_name"], g["first_detected"]),
    )


# === Phase 2.5c：CSV / JSON 匯出（round-trip：匯出 → 修改 → 匯入） ===

def get_all_nvrs_for_export(db_path: str) -> list[dict]:
    """所有 NVR 完整資料（含密碼），給 round-trip 匯出用。

    回傳格式跟 `_normalize_nvr_dict` 對齊：
        - 用 `id` 作為 key（不是 `nvr_id`）
        - tags 是 list
        - verify_ssl 是 bool

    不含 DB 內部欄位（internal id / created_at / updated_at）。

    注意：密碼以**明碼**回傳。這個函數只用在 export 路由，且 user 是
    為了把清單帶去別台環境（或備份）。若擔心安全，可改為環境變數開關。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT nvr_id AS id, name, host, port, username, password,
                   verify_ssl, site_id, tags
            FROM nvr_servers
            ORDER BY nvr_id
            """
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            # tags: JSON 字串 → list
            try:
                d["tags"] = json.loads(d["tags"] or "[]")
            except Exception:
                d["tags"] = []
            # verify_ssl: 0/1 → bool
            d["verify_ssl"] = bool(d["verify_ssl"])
            # site_id 空字串 → None（與 import 範本一致）
            if not d.get("site_id"):
                d["site_id"] = None
            # port 統一 int
            d["port"] = int(d["port"])
            out.append(d)
        return out
    finally:
        conn.close()


# === Phase 2.5b：CSV / JSON 批次匯入 ===

def bulk_create_nvrs(db_path: str, nvr_list: list[dict]) -> dict:
    """批次新增多台 NVR（all-or-nothing transaction）。

    Args:
        db_path: SQLite 路徑。
        nvr_list: list of dict（同 create_nvr 接受格式）。

    Returns:
        {"inserted": int, "internal_ids": list[int]}

    Raises:
        ValueError: 任一筆驗證失敗 → 整批 rollback。
        sqlite3.IntegrityError: id 重複 → 整批 rollback。
    """
    if not nvr_list:
        raise ValueError("沒有可匯入的 NVR")
    conn = _connect_writable(db_path)
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inserted_ids: list[int] = []
        for nvr_data in nvr_list:
            # 重複 id 檢查（在檔案內）
            cur = conn.execute(
                """
                INSERT INTO nvr_servers (
                    nvr_id, name, host, port, username, password,
                    verify_ssl, site_id, tags, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nvr_data["nvr_id"],
                    nvr_data["name"],
                    nvr_data["host"],
                    int(nvr_data.get("port", 8443)),
                    nvr_data.get("username", ""),
                    nvr_data.get("password", ""),
                    1 if nvr_data.get("verify_ssl") else 0,
                    nvr_data.get("site_id") or None,
                    json.dumps(_normalize_tags(nvr_data.get("tags", []))),
                    now_iso,
                    now_iso,
                ),
            )
            inserted_ids.append(cur.lastrowid)
        conn.commit()
        return {"inserted": len(inserted_ids), "internal_ids": inserted_ids}
    except (sqlite3.IntegrityError, sqlite3.Error, ValueError):
        conn.rollback()
        raise
    finally:
        conn.close()


def bulk_upsert_nvrs(db_path: str, nvr_list: list[dict]) -> dict:
    """批次 upsert 多台 NVR（id 存在 → 更新；不存在 → 新增）。

    給 round-trip workflow 用：匯出 → 修改 → 匯入 → 覆蓋式同步。

    Args:
        db_path: SQLite 路徑。
        nvr_list: list of dict（同 create_nvr 接受格式）。

    Returns:
        {
            "inserted": int,        # 新增的數量
            "updated": int,         # 更新的數量
            "total": int,           # 總共處理（=inserted+updated）
        }

    Raises:
        ValueError: 任一筆驗證失敗 → 整批 rollback。
    """
    if not nvr_list:
        raise ValueError("沒有可匯入的 NVR")
    conn = _connect_writable(db_path)
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # === 2026-07-30 host dedup（完整 migration）===
        # 同 (host, port) 若已有不同 nvr_id 的 row：
        #   1. 不再 hack「把舊 row 的 nvr_id 改成新 alias」（會撞 UNIQUE）
        #   2. 改用：建好 NEW row 後，把 OLD row 旗下的 FK 資料全部 migrate 過去，
        #      最後 DELETE OLD row → 保證「一台實體 NVR ↔ DB 一筆 row」
        # 否則 nvr_config.json 改了 id 就會建第二筆同 host NVR，
        # → /wall 看到重複 cam、/abnormal 看到幽靈相機、dashboard 顯示舊資料。
        collisions: list[dict] = []
        for nvr_data in nvr_list:
            target_host = nvr_data.get("host", "")
            target_port = int(nvr_data.get("port", 8443))
            new_nvr_id = nvr_data["nvr_id"]
            old_rows = conn.execute(
                """
                SELECT id FROM nvr_servers
                WHERE host=? AND port=? AND nvr_id != ?
                """,
                (target_host, target_port, new_nvr_id),
            ).fetchall()
            for row in old_rows:
                collisions.append({
                    "new_nvr_id": new_nvr_id,
                    "old_id": row["id"],
                })
        # 統計既有 id（用 nvr_id 比對，不是 internal id）
        existing = {
            r["nvr_id"]
            for r in conn.execute("SELECT nvr_id FROM nvr_servers").fetchall()
        }
        inserted = 0
        updated = 0
        for nvr_data in nvr_list:
            is_existing = nvr_data["nvr_id"] in existing
            # 用 ON CONFLICT(nvr_id) DO UPDATE：id 重複就 update
            # 關鍵：password 若使用者未填（空字串），覆蓋式匯入會清空密碼
            # → 解法：若 password 為空且 is_existing，跳過 password 欄位
            # enabled：CSV/JSON 沒指定時預設 1（啟用）；明確給 0 才停用
            enabled_val = 1 if nvr_data.get("enabled", True) else 0
            if is_existing and not nvr_data.get("password"):
                # 不更新 password（保留原值）
                conn.execute(
                    """
                    INSERT INTO nvr_servers (
                        nvr_id, name, host, port, username, password,
                        verify_ssl, site_id, tags, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(nvr_id) DO UPDATE SET
                        name = excluded.name,
                        host = excluded.host,
                        port = excluded.port,
                        username = excluded.username,
                        verify_ssl = excluded.verify_ssl,
                        site_id = excluded.site_id,
                        tags = excluded.tags,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (
                        nvr_data["nvr_id"],
                        nvr_data["name"],
                        nvr_data["host"],
                        int(nvr_data.get("port", 8443)),
                        nvr_data.get("username", ""),
                        1 if nvr_data.get("verify_ssl") else 0,
                        nvr_data.get("site_id") or None,
                        json.dumps(_normalize_tags(nvr_data.get("tags", []))),
                        enabled_val,
                        now_iso,
                        now_iso,
                    ),
                )
            else:
                # 新增 或 完整更新（含 password）
                conn.execute(
                    """
                    INSERT INTO nvr_servers (
                        nvr_id, name, host, port, username, password,
                        verify_ssl, site_id, tags, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(nvr_id) DO UPDATE SET
                        name = excluded.name,
                        host = excluded.host,
                        port = excluded.port,
                        username = excluded.username,
                        password = excluded.password,
                        verify_ssl = excluded.verify_ssl,
                        site_id = excluded.site_id,
                        tags = excluded.tags,
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (
                        nvr_data["nvr_id"],
                        nvr_data["name"],
                        nvr_data["host"],
                        int(nvr_data.get("port", 8443)),
                        nvr_data.get("username", ""),
                        nvr_data.get("password", ""),
                        1 if nvr_data.get("verify_ssl") else 0,
                        nvr_data.get("site_id") or None,
                        json.dumps(_normalize_tags(nvr_data.get("tags", []))),
                        enabled_val,
                        now_iso,
                        now_iso,
                    ),
                )
            if is_existing:
                updated += 1
            else:
                inserted += 1

        # === Phase 3: 處理 collisions（host:port 同但 nvr_id 不同）===
        # 把 OLD row 旗下的 FK 資料搬去 NEW row，然後 DELETE OLD。
        # 注意：cameras / recording_status / camera_snapshots 有 UNIQUE(nvr_id, *)
        # 若 OLD 和 NEW 都有同 device 的 row，UPDATE 會撞 UNIQUE；
        # 用 INSERT OR IGNORE + DELETE 模式安全處理。
        PLAIN_TABLES = [
            ("events", "nvr_id"),
            ("image_health_checks", "nvr_server_id"),
            ("nvr_failure_log", "nvr_internal_id"),
        ]
        UNIQ_TABLES = [
            # (table, fk_col, uniq_cols)
            ("cameras", "nvr_id", ["device_id"]),
            ("recording_status", "nvr_id", ["camera_id"]),
            ("camera_snapshots", "nvr_id", ["camera_id"]),
        ]
        for c in collisions:
            new_row = conn.execute(
                "SELECT id FROM nvr_servers WHERE nvr_id=?",
                (c["new_nvr_id"],),
            ).fetchone()
            if not new_row:
                continue
            new_id = new_row["id"]
            old_id = c["old_id"]
            if new_id == old_id:
                continue

            # 無 UNIQUE 撞的表：直接 UPDATE
            for tbl, col in PLAIN_TABLES:
                conn.execute(
                    f"UPDATE {tbl} SET {col}=? WHERE {col}=?",
                    (new_id, old_id),
                )

            # 有 UNIQUE(nvr_id, uniq_cols) 的表：
            # 1) INSERT OR IGNORE 從 OLD → NEW（重複就略過，保留 NEW 原有的）
            # 2) DELETE 剩下的 OLD row
            for tbl, col, uniq_cols in UNIQ_TABLES:
                uniq_select = ", ".join(uniq_cols)
                uniq_list = ", ".join(
                    f"{uc} = excluded.{uc}" for uc in uniq_cols
                )
                # 從 OLD 撈出每列（除了 nvr_id）並 INSERT OR IGNORE 到 NEW
                other_cols = [
                    r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                    if r[1] not in ("id", col)
                ]
                cols_csv = ", ".join(other_cols)
                sel_csv = ", ".join(other_cols)
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {tbl} ({col}, {cols_csv})
                    SELECT ?, {sel_csv} FROM {tbl}
                    WHERE {col} = ?
                    """,
                    (new_id, old_id),
                )
                # 刪掉 OLD 剩下的（已被 INSERT 走或本來就要丟）
                conn.execute(
                    f"DELETE FROM {tbl} WHERE {col}=?",
                    (old_id,),
                )

            conn.execute("DELETE FROM nvr_servers WHERE id=?", (old_id,))

        conn.commit()
        return {"inserted": inserted, "updated": updated, "total": inserted + updated}
    except (sqlite3.IntegrityError, sqlite3.Error, ValueError):
        conn.rollback()
        raise
    finally:
        conn.close()


# === Events ===
def get_events_filtered(
    db_path: str,
    *,
    hours: int = 24,
    nvr_id: int | None = None,
    topic: str | None = None,
    status: str = "all",  # "open" / "resolved" / "all"
    limit: int = 100,
) -> list[dict]:
    """
    過濾查詢 events（Phase 1 起含 resolved 狀態）。

    Args:
        db_path: SQLite 路徑。
        hours: 只取 N 小時內（detected_at 基準）。
        nvr_id: 只取指定 NVR（內部 id）。
        topic: 只取特定 event_topic（LIKE 匹配）。
        status: "open"（resolved_at IS NULL）/"resolved"（resolved_at NOT NULL）/"all"。
        limit: 上限筆數。

    Returns:
        list[dict]，每筆含 id / event_topic / device_id / camera_name /
        occurred_at / detected_at / resolved_at / nvr_id / nvr_name /
        scan_run_id / status（衍生欄位，'open' 或 'resolved'）
    """
    conn = _connect(db_path)
    try:
        sql = """
            SELECT e.id, e.event_topic, e.device_id, c.camera_name,
                   e.occurred_at, e.detected_at, e.resolved_at,
                   n.nvr_id, n.name AS nvr_name, s.id AS scan_run_id
            FROM events e
            LEFT JOIN cameras c
                ON e.nvr_id = c.nvr_id AND e.device_id = c.device_id
            JOIN nvr_servers n ON e.nvr_id = n.id
            JOIN scan_runs s ON e.scan_run_id = s.id
            WHERE e.detected_at >= ?
        """
        params: list[Any] = []
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        params.append(cutoff)
        if nvr_id is not None:
            sql += " AND e.nvr_id = ?"
            params.append(nvr_id)
        if topic:
            sql += " AND e.event_topic LIKE ?"
            params.append(f"%{topic}%")
        if status == "open":
            sql += " AND e.resolved_at IS NULL"
        elif status == "resolved":
            sql += " AND e.resolved_at IS NOT NULL"
        # status == "all" → 不加條件
        sql += " ORDER BY e.detected_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 衍生欄位：給 UI 用（避免 template 邏輯外洩）
            d["status"] = "resolved" if d["resolved_at"] else "open"
            out.append(d)
        return out
    finally:
        conn.close()


def get_devices_paginated(
    db_path: str, *, page: int = 1, per_page: int = 50,
    nvr_filter: str = "", status_filter: str = "",
) -> tuple[list[dict], int]:
    """Phase 2.8（Arisan）Phase #5：跨 NVR 設備總覽表（分頁）。

    Args:
        nvr_filter: NVR internal id 字串（空 = 不過濾）
        status_filter: 'online' | 'signal_lost' | 'no_signal'（空 = 全部）

    Returns:
        (rows, total)；rows 每筆含 nvr_name / device_id / camera_name /
        latest_topic / latest_topic_zh / category / nvr_id。
    """
    where = ["1=1"]
    params: list = []
    if nvr_filter:
        where.append("c.nvr_id = ?")
        params.append(int(nvr_filter))
    if status_filter:
        if status_filter == "online":
            # online = 沒事件 OR 事件不在訊號/無訊號清單
            placeholders = ",".join("?" for _ in range(len(_SIGNAL_LOST_TOPICS | _NO_SIGNAL_TOPICS)))
            where.append(
                f"(latest.event_topic IS NULL OR latest.event_topic NOT IN ({placeholders}))"
            )
            params.extend(sorted(_SIGNAL_LOST_TOPICS | _NO_SIGNAL_TOPICS))
        elif status_filter == "signal_lost":
            placeholders = ",".join("?" for _ in range(len(_SIGNAL_LOST_TOPICS)))
            where.append(f"latest.event_topic IN ({placeholders})")
            params.extend(sorted(_SIGNAL_LOST_TOPICS))
        elif status_filter == "no_signal":
            placeholders = ",".join("?" for _ in range(len(_NO_SIGNAL_TOPICS)))
            where.append(f"latest.event_topic IN ({placeholders})")
            params.extend(sorted(_NO_SIGNAL_TOPICS))

    where_sql = " AND ".join(where)

    conn = _connect(db_path)
    try:
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM cameras c
            LEFT JOIN (
                SELECT e.nvr_id, e.device_id, e.event_topic
                FROM events e
                WHERE e.resolved_at IS NULL
                  AND e.id IN (
                    SELECT MAX(e2.id) FROM events e2
                    WHERE e2.resolved_at IS NULL
                    GROUP BY e2.nvr_id, e2.device_id
                  )
            ) latest ON latest.nvr_id = c.nvr_id AND latest.device_id = c.device_id
            WHERE {where_sql}
            """,
            params,
        ).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT c.nvr_id, n.name AS nvr_name,
                   c.device_id, c.camera_name,
                   latest.event_topic AS latest_topic
            FROM cameras c
            JOIN nvr_servers n ON n.id = c.nvr_id
            LEFT JOIN (
                SELECT e.nvr_id, e.device_id, e.event_topic
                FROM events e
                WHERE e.resolved_at IS NULL
                  AND e.id IN (
                    SELECT MAX(e2.id) FROM events e2
                    WHERE e2.resolved_at IS NULL
                    GROUP BY e2.nvr_id, e2.device_id
                  )
            ) latest ON latest.nvr_id = c.nvr_id AND latest.device_id = c.device_id
            WHERE {where_sql}
            ORDER BY n.name, c.camera_name, c.device_id
            LIMIT ? OFFSET ?
            """,
            params + [per_page, offset],
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        topic = r["latest_topic"]
        if topic in _SIGNAL_LOST_TOPICS:
            cat = "signal_lost"
        elif topic in _NO_SIGNAL_TOPICS:
            cat = "no_signal"
        else:
            cat = "online"
        out.append({
            "nvr_id": r["nvr_id"],
            "nvr_name": r["nvr_name"],
            "device_id": r["device_id"],
            "camera_name": r["camera_name"],
            "latest_topic": topic,
            "latest_topic_zh": get_event_label_zh(db_path, topic) if topic else None,
            "category": cat,
        })
    return out, total


def get_device_detail(db_path: str, device_id: str) -> dict | None:
    """Phase 2.8（Arisan）Phase #5：單台 cam 詳情（跨 NVR，可能多筆同 device_id）。

    Returns:
        dict 含 cam info + 最新未解事件 + last health check；
        多筆同 device_id 取 latest_seen 最新那一筆。
        找不到回 None。
    """
    conn = _connect(db_path)
    try:
        # Phase 2.8 image_health_checks 表可能不存在（DB 是舊版、還沒跑 worker）
        try:
            has_health_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_health_checks'"
            ).fetchone() is not None
        except sqlite3.OperationalError:
            has_health_table = False
        row = conn.execute(
            """
            SELECT c.id, c.nvr_id, c.device_id, c.camera_name, c.last_seen_at,
                   n.name AS nvr_name, n.host AS nvr_host,
                   (
                       SELECT e.event_topic FROM events e
                       WHERE e.device_id = c.device_id AND e.resolved_at IS NULL
                       ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1
                   ) AS latest_topic,
                   {health_metrics} AS latest_metrics,
                   {health_flags} AS latest_flags,
                   {health_checked_at} AS last_checked_at
            FROM cameras c
            JOIN nvr_servers n ON n.id = c.nvr_id
            WHERE c.device_id = ?
            ORDER BY c.last_seen_at DESC, c.id DESC
            LIMIT 1
            """.format(
                health_metrics=(
                    "(SELECT h.metrics_json FROM image_health_checks h "
                    "WHERE h.camera_id = c.device_id ORDER BY h.id DESC LIMIT 1)"
                    if has_health_table else "NULL"
                ),
                health_flags=(
                    "(SELECT h.flags_json FROM image_health_checks h "
                    "WHERE h.camera_id = c.device_id ORDER BY h.id DESC LIMIT 1)"
                    if has_health_table else "NULL"
                ),
                health_checked_at=(
                    "(SELECT h.checked_at_utc FROM image_health_checks h "
                    "WHERE h.camera_id = c.device_id ORDER BY h.id DESC LIMIT 1)"
                    if has_health_table else "NULL"
                ),
            ),
            (device_id,),
        ).fetchone()
        if not row:
            return None
        info = dict(row)
        info["latest_topic_zh"] = (
            get_event_label_zh(db_path, info["latest_topic"]) if info["latest_topic"] else None
        )
        info["category"] = (
            "signal_lost" if info["latest_topic"] in _SIGNAL_LOST_TOPICS
            else "no_signal" if info["latest_topic"] in _NO_SIGNAL_TOPICS
            else "online"
        )
        info["recording_status"] = get_recording_status_for_camera(
            db_path, info["nvr_id"], device_id
        )
        return info
    finally:
        conn.close()


def get_camera_health_history(
    db_path: str, device_id: str, limit: int = 50,
) -> list[dict]:
    """Phase 2.8（Arisan）Phase #5：單台 cam 健康歷史（image_health_checks）。"""
    conn = _connect(db_path)
    try:
        # Phase 2.8 表格可能在舊 DB 不存在 → 回空 list
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_health_checks'"
        ).fetchone() is None:
            return []
        rows = conn.execute(
            """
            SELECT id, checked_at_utc, metrics_json, flags_json,
                   triggered_event_ids
            FROM image_health_checks
            WHERE camera_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (device_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            import json as _json
            try:
                metrics = _json.loads(r["metrics_json"]) if r["metrics_json"] else {}
            except Exception:
                metrics = {}
            try:
                flags = _json.loads(r["flags_json"]) if r["flags_json"] else []
            except Exception:
                flags = []
            out.append({
                "id": r["id"],
                "checked_at_utc": r["checked_at_utc"],
                "metrics": metrics,
                "flags": flags,
            })
        return out
    finally:
        conn.close()


def create_discover_session(db_path: str, *, cidr: str, port: int) -> int:
    """Phase 2.8（Arisan）Phase #5：建 discover session stub（Phase #6 才填結果）。

    Returns: 新 session id。
    """
    import json as _json
    conn = _connect_writable(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO discover_sessions
                (started_at_utc, cidr, port, results_json, status)
            VALUES (datetime('now'), ?, ?, ?, 'pending')
            """,
            (cidr, port, _json.dumps([])),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_discover_session(
    db_path: str,
    session_id: int,
    *,
    results: list[dict],
    status: str,
) -> None:
    """Phase 2.8（Arisan）Phase #6：寫回 discover_sessions 結果 + status + finished_at_utc。"""
    import json as _json
    conn = _connect_writable(db_path)
    try:
        # 若 finished_at_utc 為 NULL 才寫（保留 started→running→completed 的時間序）
        conn.execute(
            """
            UPDATE discover_sessions
            SET results_json = ?, status = ?,
                finished_at_utc = COALESCE(finished_at_utc, datetime('now'))
            WHERE id = ?
            """,
            (_json.dumps(results), status, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_existing_nvr_ips(db_path: str) -> set[str]:
    """Phase 2.8（Arisan）Phase #6：所有 nvr_servers 的 host set（給 discover skip 用）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT host FROM nvr_servers").fetchall()
        return {r["host"] for r in rows}
    finally:
        conn.close()


def get_session_port(db_path: str, session_id: int) -> int:
    """Phase 2.8（Arisan）Phase #6：讀 discover_sessions.port（給 probe 用）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT port FROM discover_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return 8443
        return int(row["port"] or 8443)
    finally:
        conn.close()


def get_discover_session(db_path: str, session_id: int) -> dict | None:
    """Phase 2.8（Arisan）Phase #5：讀 discover session（含 results_json 解析）。"""
    import json as _json
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, started_at_utc, finished_at_utc, cidr, port, "
            "results_json, status FROM discover_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        info = dict(row)
        try:
            info["results"] = _json.loads(info.pop("results_json") or "[]")
        except Exception:
            info["results"] = []
        return info
    finally:
        conn.close()


def get_event_label_zh(db_path: str, topic: str) -> str:
    """Phase 2.8（Arisan）Phase #5 補：event_kind_catalog 表驅動 i18n。

    從 event_kind_catalog 表查 name_zh。
    - topic 在 catalog 內 → 回傳 name_zh
    - 不在 → 回傳原文 topic（fallback，不丟）
    - 空字串 → 回傳空字串
    - topic 帶前後空白 → trim 後再查
    - DB 不存在 → fallback 原文（不 raise）
    """
    if not topic or not topic.strip():
        return topic or ""
    topic = topic.strip()
    try:
        conn = _connect(db_path)
    except FileNotFoundError:
        return topic
    try:
        row = conn.execute(
            "SELECT name_zh FROM event_kind_catalog WHERE event_topic = ?",
            (topic,),
        ).fetchone()
        if row and row["name_zh"]:
            return row["name_zh"]
        return topic
    finally:
        conn.close()


def get_all_topics(db_path: str) -> list[str]:
    """取得所有曾出現的 event_topic（給 filter dropdown 用）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT event_topic FROM events ORDER BY event_topic"
        ).fetchall()
        return [r["event_topic"] for r in rows]
    finally:
        conn.close()


# === Phase 1 Step 3b：Ad-hoc 唯讀 SELECT（給 /query 頁） ===

# 黑名單 keyword（大小寫不敏感）
_FORBIDDEN_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "ALTER", "CREATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "LOAD",
    "SAVEPOINT", "BEGIN", "COMMIT", "ROLLBACK", "ANALYZE",
})


def _strip_sql_comments(sql: str) -> str:
    """把 `--` 單行註解與 `/* */` 區塊註解移除（保留字串常值內的）。"""
    import re
    # 區塊註解
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # 單行註解（簡化：不處理字串內的「--」；查詢裡通常不會有字串常值）
    lines = []
    for line in sql.split("\n"):
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        lines.append(line)
    return "\n".join(lines)


def _validate_readonly_sql(sql: str) -> str:
    """
    驗證 SQL 是唯讀 SELECT（拒絕多 statement、非 SELECT/WITH、危險 keyword）。

    Args:
        sql: 使用者輸入的 SQL。

    Returns:
        cleaned SQL（去除註解、首尾空白）。

    Raises:
        ValueError: 不通過任何規則（含原因）。
    """
    cleaned = _strip_sql_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("SQL 不能為空")

    # 1. 必須以 SELECT 或 WITH（CTE）開頭
    upper = cleaned.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError(
            "只允許 SELECT / WITH ... SELECT 查詢。"
            f"（你的查詢以「{cleaned[:30]}...」開頭）"
        )

    # 2. 拒絕多 statement（簡化：允許結尾分號但拒絕中段分號）
    #    已 strip 末尾分號；中段的分號會被 SQLite 視為 statement separator
    if ";" in cleaned:
        raise ValueError("不支援多 statement 查詢（請只貼一段 SQL）")

    # 3. 黑名單 keyword（用 word boundary 比對）
    import re
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            raise ValueError(f"禁止使用 {kw}（僅允許唯讀 SELECT）")

    # 4. EXPLAIN / EXPLAIN QUERY PLAN 屬唯讀，但可能洩漏資訊 → 也擋
    if re.search(r"\bEXPLAIN\b", upper):
        raise ValueError("禁止使用 EXPLAIN（僅允許唯讀 SELECT）")

    return cleaned


def run_readonly_query(
    db_path: str,
    sql: str,
    *,
    max_rows: int = 500,
) -> dict:
    """
    執行使用者提供的唯讀 SELECT 查詢（Phase 1 Step 3b）。

    安全：
        - 白名單：必須 SELECT/WITH 開頭
        - 黑名單：拒絕常見破壞性 keyword
        - 拒絕多 statement
        - PRAGMA query_only = ON（SQLite 層強制）
        - 限制 max_rows（超過會標記 truncated）

    Args:
        db_path: SQLite 路徑。
        sql: 使用者 SQL。
        max_rows: 回傳上限（超過則標記 truncated=True）。

    Returns:
        {
            "columns": list[str],
            "rows": list[tuple],
            "row_count": int,
            "truncated": bool,
        }

    Raises:
        ValueError: SQL 不通過驗證（訊息給 UI 顯示）。
    """
    cleaned = _validate_readonly_sql(sql)
    conn = _connect(db_path)
    try:
        cur = conn.execute(cleaned)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        # 轉 tuple 以利 JSON / template 序列化
        rows = [tuple(r) for r in rows]
        return {
            "columns": cols,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
    except ValueError:
        raise
    except sqlite3.Error as exc:
        # 把 SQLite 錯誤轉成 ValueError 統一給 UI 顯示
        raise ValueError(f"SQLite 錯誤：{exc}") from exc
    finally:
        conn.close()



def mark_scan_interrupted(run_id: int, db_path: str = "nvr_scan.db") -> bool:
    """把 scan_runs.status='running' 標為 'failed'，並寫 finished_at。

    用於：Web UI 接收到 SIGTERM/SIGINT 時，背景 scan thread 來不及正常
    finish_scan_run 會留下孤兒 row，此函式由 signal handler 呼叫清理。

    Args:
        run_id: scan_runs.id
        db_path: SQLite 路徑

    Returns:
        True if row was updated, False if not found
    """
    conn = _connect_writable(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE scan_runs
            SET status = 'failed',
                finished_at = ?,
                error_message = COALESCE(error_message, 'process interrupted by signal')
            WHERE id = ? AND status = 'running'
            """,
            (datetime.now(timezone.utc).isoformat(), run_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# Alias: 給 signal handler 從 web.app 呼叫時少打字
mark_scan_interrupted_by_path = mark_scan_interrupted


def mark_all_running_as_interrupted(db_path: str = "nvr_scan.db") -> int:
    """把 DB 內所有 status='running' 的 scan_run 標為 failed。

    用於 SIGTERM/SIGINT 收尾。回傳受影響的 row 數。
    """
    conn = _connect_writable(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE scan_runs
            SET status = 'failed',
                finished_at = ?,
                error_message = COALESCE(error_message, 'process interrupted by signal')
            WHERE status = 'running'
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
