"""
web/coverage.py
===============
8555 錄影覆蓋熱區（Spec F）— 純邏輯模組。

不碰 Flask / DB — 純資料處理：解析 NVR /timeline 回傳、計算完整率。
回傳 dict 給 web/clips_app.py 路由序列化。

依賴：
    - web/timeline.py（既有：`parse_timeline_response`, `compute_completeness`）
    - nvr_scanner.AvigilonScanner.get_timeline（既有，注入測試）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from web.timeline import parse_timeline_response, compute_completeness, _clip_to_window


def _parse_iso_utc(s: str) -> datetime:
    """解析 ISO 8601 字串（可能帶 Z 或 +00:00 結尾）回 tz-aware UTC datetime。"""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_records_from_timeline_response(payload: dict | None) -> dict[str, list[tuple[datetime, datetime]]]:
    """
    從 NVR /timeline 原始 payload 抽出 {camera_id: [(start, end), ...]}。

    復用 web.timeline.parse_timeline_response（已在前幾版驗證）。
    純 wrapper：保留 API 進入點讓 8555 端不直接 import web.timeline。
    """
    return parse_timeline_response(payload)


def compute_per_camera_completeness(
    records: list[tuple[datetime, datetime]] | list[tuple[str, str]],
    start_iso: str,
    end_iso: str,
) -> float:
    """計算指定視窗內的錄影完整率（0.0 ~ 1.0）。

    records 接受 datetime tuples 或 ISO 8601 字串 tuples（兩種都支援）。
    """
    start = _parse_iso_utc(start_iso)
    end = _parse_iso_utc(end_iso)
    parsed_records: list[tuple[datetime, datetime]] = []
    for rec in records:
        s, e = rec
        if isinstance(s, str):
            s = _parse_iso_utc(s)
        if isinstance(e, str):
            e = _parse_iso_utc(e)
        parsed_records.append((s, e))
    return compute_completeness(parsed_records, start, end)


@dataclass
class CoverageCamera:
    """單台 cam 的 coverage 摘要（給 web.coverage.fetch_coverage_from_nvr 用）。"""
    cam_id: str
    camera_name: str
    records: list[tuple[datetime, datetime]]  # 原始 (start, end) tuples；serializer 自行轉 iso
    completeness: float  # 0.0 ~ 1.0


def fetch_coverage_from_nvr(
    *,
    nvr: dict,
    cameras: list[dict],
    start_iso: str,
    end_iso: str,
    timeline_fetcher: Callable[..., dict],
) -> dict:
    """
    抓 1 台 NVR 所有 cam 的 timeline，回傳統一 dict。

    Args:
        nvr: dict（至少含 host / port / nvr_id / user_nonce / user_key）
        cameras: list[dict]（每個 dict 有 device_id + camera_name）
        start_iso: 視窗開始（ISO 8601）
        end_iso: 視窗結束（ISO 8601）
        timeline_fetcher: 注入的 AvigilonScanner.get_timeline 函式（測試用）；
                          會以 (cam_id, start_iso, end_iso) 為位置參數呼叫。

    Returns:
        {
            "nvr_id": str,
            "start": str,
            "end": str,
            "cameras": [
                {"cam_id": str, "camera_name": str, "records": [[start, end], ...], "completeness": 0.0~1.0}
            ]
        }
    """
    # 1. 逐一抓每台 cam 的 timeline（NVR 端支援 cameraIds 帶逗號分隔；先用單台抓保持簡單）
    parsed_all: dict[str, list[tuple[datetime, datetime]]] = {}
    for cam in cameras:
        device_id = cam["device_id"]
        try:
            raw = timeline_fetcher(device_id, start_iso, end_iso)
        except Exception:
            # 某台 cam 抓失敗 → 該 cam 沒有資料，但不讓整體 500
            parsed_all[device_id] = []
            continue
        per_cam = parse_records_from_timeline_response(raw)
        parsed_all[device_id] = per_cam.get(device_id, [])

    # 2. 計算每 cam 完整率；同時把 records 裁切到 [start, end] 視窗內
    #    （NVR /timeline 端會忽略 from/to、回傳視窗外舊資料；不 clip 的話前端會誤繪綠帶）
    window_start = _parse_iso_utc(start_iso)
    window_end = _parse_iso_utc(end_iso)
    out_cameras = []
    for cam in cameras:
        device_id = cam["device_id"]
        raw_records = parsed_all.get(device_id, [])
        clipped_records: list[tuple[datetime, datetime]] = []
        for rec in raw_records:
            clipped = _clip_to_window(rec, window_start, window_end)
            if clipped is not None:
                clipped_records.append(clipped)
        records_iso = [[s.isoformat(), e.isoformat()] for s, e in clipped_records]
        completeness = compute_per_camera_completeness(clipped_records, start_iso, end_iso)
        out_cameras.append({
            "cam_id": device_id,
            "camera_name": cam.get("camera_name", device_id),
            "records": records_iso,
            "completeness": round(completeness, 4),
        })

    return {
        "nvr_id": nvr.get("nvr_id", ""),
        "start": start_iso,
        "end": end_iso,
        "cameras": out_cameras,
    }
