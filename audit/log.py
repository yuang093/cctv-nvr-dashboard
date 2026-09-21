"""Week 5 #015：audit log 寫入與 rotation helper。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta


def write_audit_event(
    db_path: str,
    event: str,
    ip: str,
    *,
    user_id: int | None = None,
    user_agent: str | None = None,
    payload: dict | None = None,
) -> None:
    """寫入一筆 audit event。

    Args:
        db_path: SQLite 檔路徑
        event: 事件名稱（login / login_failed / logout / access_denied / query / toggle_nvr / request）
        ip: 來源 IP
        user_id: 操作者 user id（ops_auto 可為 None）
        user_agent: 瀏覽器 UA
        payload: 額外資訊（會序列化成 JSON）

    Note:
        若 audit_log 表不存在（舊 DB 升級過渡期）→ 靜默跳過，不阻擋主流程。
    """
    payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
    created_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        # 確認表存在（舊 DB 升級過渡期）
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if not existing:
            return  # 表不存在，靜默跳過
        conn.execute(
            """
            INSERT INTO audit_log (user_id, event, ip, user_agent, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, event, ip, user_agent, payload_json, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def rotate_audit_log(db_path: str, retention_days: int = 90) -> int:
    """刪除超過 retention_days 的 audit log，回傳刪除筆數。

    預期在 dump_and_compress 之後呼叫（先把要刪除的 rows 封存）。
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
        deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()
