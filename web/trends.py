"""
web/trends.py
=============
Spec G: Cam 健康趨勢圖的純 DB 查詢層。

設計：
    - 純函式（無 Flask context、無全域 state）
    - db_path 顯式傳入（與 web.db 一致風格）
    - 回傳 dataclass，方便 template 用 attribute access
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class HealthBin:
    """單一時段 bin 的健康統計。"""
    start_utc: str           # ISO 8601 e.g. "2026-08-05T14:00:00Z"
    online_pct: float        # 0.0-100.0
    frozen_pct: float        # 0.0-100.0
    underexposed_pct: float  # 0.0-100.0（spec 原名 dark；改用程式碼既有的 underexposed）
    sample_count: int        # 該 bin 內 image_health 記錄數


@dataclass(frozen=True)
class CamHealthSummary:
    """一台 cam 的健康摘要。"""
    cam_id: str
    cam_name: str
    nvr_id: str
    nvr_name: str
    bins: list[HealthBin]
    abnormal_bins: int       # frozen/underexposed/offline 任一 > 0 的 bin 數


def _connect(db_path: str) -> sqlite3.Connection:
    """與 web.db._connect 同風格的唯讀連線。"""
    p = Path(db_path)
    if not p.exists() and db_path != ":memory:":
        raise FileNotFoundError(f"DB 檔不存在：{db_path}")
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA query_only = ON")
    return conn


def _bin_size_hours(range_hours: int) -> int:
    """24h → 1h bins；7d → 24h daily bins。"""
    if range_hours == 24:
        return 1
    if range_hours == 168:  # 7d
        return 24
    raise ValueError(f"range_hours 必須是 24 或 168，got {range_hours!r}")


def _truncate_to_bin(t: datetime, bin_size_h: int) -> datetime:
    """把時間截到該 bin 起始（UTC, 去除時區情報）。"""
    if bin_size_h >= 24:
        return t.replace(hour=0, minute=0, second=0, microsecond=0)
    return t.replace(minute=0, second=0, microsecond=0, hour=(t.hour // bin_size_h) * bin_size_h)


def _bin_start_iso(bin_start: datetime) -> str:
    return bin_start.strftime("%Y-%m-%dT%H:00:00Z") if bin_start.hour or bin_start.minute \
        else bin_start.strftime("%Y-%m-%dT00:00:00Z")


def compute_health_timeseries(
    db_path: str,
    cam_id: str,
    range_hours: int,
) -> list[HealthBin]:
    """從 image_health_checks 算 cam 的時間序列健康資料。

    24h → 24 個 1-hour bins
    7d  → 7 個 24-hour bins（每天一個聚合）

    演算法：
        1. 從 image_health_checks 取該 cam 的所有 record（時間區間）
        2. 用 bin index 把 record 分配到 N 個 bucket
        3. 每 bucket 算 online/frozen/underexposed %

    Args:
        db_path: SQLite DB 路徑
        cam_id: 相機 device_id
        range_hours: 24 或 168（7d）

    Returns:
        由舊到新排序的 list[HealthBin]，長度 = range_hours / bin_size_hours
    """
    bin_h = _bin_size_hours(range_hours)
    n_bins = range_hours // bin_h
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(hours=range_hours)

    # 算每個 bin 的起始時間（從最舊到最新）
    bin_starts: list[datetime] = []
    # 以窗口起點對齊到 bin 邊界（每 24h 或 1h）
    aligned_start = _truncate_to_bin(window_start, bin_h)
    for i in range(n_bins):
        bin_starts.append(aligned_start + timedelta(hours=i * bin_h))

    # 初始化 bucket
    buckets: list[dict] = [
        {"total": 0, "frozen": 0, "underexposed": 0} for _ in range(n_bins)
    ]

    # 查詢該 cam 在窗口內的所有 record
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT checked_at_utc, metrics_json
            FROM image_health_checks
            WHERE camera_id = ?
              AND checked_at_utc >= ?
            ORDER BY checked_at_utc ASC
            """,
            (cam_id, window_start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        checked_at = row["checked_at_utc"]
        # parse "2026-08-05T14:23:01Z" 形式
        ts = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        # 對應到 bin index
        delta_h = (ts - aligned_start).total_seconds() / 3600.0
        idx = int(delta_h // bin_h)
        if 0 <= idx < n_bins:
            buckets[idx]["total"] += 1
            # metrics_json parse（容錯 fallback）
            try:
                metrics = json.loads(row["metrics_json"])
                if not isinstance(metrics, dict):
                    raise ValueError("metrics 非 dict")
            except (json.JSONDecodeError, ValueError, TypeError):
                # 壞 JSON 視為「非 frozen、非 underexposed」（不 crash）
                continue
            if metrics.get("is_frozen") is True:
                buckets[idx]["frozen"] += 1
            if metrics.get("is_underexposed") is True:
                buckets[idx]["underexposed"] += 1

    # 組裝 HealthBin list
    result: list[HealthBin] = []
    for i, b in enumerate(buckets):
        total = b["total"]
        result.append(HealthBin(
            start_utc=_bin_start_iso(bin_starts[i]),
            online_pct=100.0 if total > 0 else 0.0,
            frozen_pct=(b["frozen"] / total * 100.0) if total > 0 else 0.0,
            underexposed_pct=(b["underexposed"] / total * 100.0) if total > 0 else 0.0,
            sample_count=total,
        ))
    return result