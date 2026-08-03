"""
web/timeline.py
===============
錄影時間軸（/timeline）純函式工具。

不碰 NVR session / DB / Flask，純資料處理（時間區間運算）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable


def _parse_iso_utc(s: str) -> datetime:
    """解析 NVR 回傳的 ISO 8601 字串（可能帶 Z 或 +00:00 結尾）回 tz-aware UTC datetime。"""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_timeline_response(data: dict | None) -> dict[str, list[tuple[datetime, datetime]]]:
    """
    從 NVR `/timeline` 回傳抽出 {camera_id: [(start, end), ...]}。

    容錯處理：
    - 空 / None 輸入 → 空 dict
    - 兼容 `timelines` 直接在根，或包在 `result.timelines`
    - 缺 cameraId 的 timeline 跳過
    - 缺 start / end 的 record 跳過
    """
    if not data:
        return {}

    payload = data
    if "result" in data and isinstance(data["result"], dict):
        payload = data["result"]

    timelines = payload.get("timelines") or []
    if not isinstance(timelines, list):
        return {}

    out: dict[str, list[tuple[datetime, datetime]]] = {}
    for entry in timelines:
        if not isinstance(entry, dict):
            continue
        cam_id = entry.get("cameraId")
        if not cam_id:
            continue
        records = entry.get("record") or []
        if not isinstance(records, list):
            out[str(cam_id)] = []
            continue
        parsed: list[tuple[datetime, datetime]] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            start_s = r.get("start")
            end_s = r.get("end")
            if not start_s or not end_s:
                continue
            parsed.append((_parse_iso_utc(start_s), _parse_iso_utc(end_s)))
        out[str(cam_id)] = parsed
    return out


def _clip_to_window(
    record: tuple[datetime, datetime],
    window_start: datetime,
    window_end: datetime,
) -> tuple[datetime, datetime] | None:
    """把一條 record 與視窗求交集；無交集回 None。"""
    rs, re = record
    s = max(rs, window_start)
    e = min(re, window_end)
    if e <= s:
        return None
    return (s, e)


def _merge_records(
    records: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """把重疊 / 相鄰的 record 合併成不重疊區段。"""
    sorted_recs = sorted(records, key=lambda r: r[0])
    merged: list[tuple[datetime, datetime]] = []
    for r in sorted_recs:
        if not merged:
            merged.append(r)
            continue
        last_s, last_e = merged[-1]
        if r[0] <= last_e:
            merged[-1] = (last_s, max(last_e, r[1]))
        else:
            merged.append(r)
    return merged


def compute_completeness(
    records: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> float:
    """
    計算指定視窗內的錄影完整率（0.0 ~ 1.0）。

    容錯：
    - window 為 0 秒 → 回 0.0（避免除以 0）
    - record 超出視窗的部分會裁切到視窗內
    - 重疊的 record 視為一段（不重複計算）
    """
    if window_end <= window_start:
        return 0.0

    window_seconds = (window_end - window_start).total_seconds()
    if window_seconds <= 0:
        return 0.0

    covered = 0.0
    for rec in records:
        clipped = _clip_to_window(rec, window_start, window_end)
        if clipped is None:
            continue
        covered += (clipped[1] - clipped[0]).total_seconds()
    return min(covered / window_seconds, 1.0)


def compute_missing_segments(
    records: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """
    從指定視窗內的錄影 record 算出缺口清單。

    重疊 record 會先合併才計算 gap，否則會誤判。
    """
    if window_end <= window_start:
        return []

    # 1) 裁切到視窗內
    clipped: list[tuple[datetime, datetime]] = []
    for rec in records:
        c = _clip_to_window(rec, window_start, window_end)
        if c is not None:
            clipped.append(c)

    # 2) 排序 + 合併
    merged = _merge_records(clipped)

    # 3) 在視窗內掃 gap
    gaps: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for s, e in merged:
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < window_end:
        gaps.append((cursor, window_end))
    return gaps
