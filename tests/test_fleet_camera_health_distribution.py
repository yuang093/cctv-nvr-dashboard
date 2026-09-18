"""
tests/test_fleet_camera_health_distribution.py
================================================
Spec B（2026-07-31）：/fleet 頁相機健康分布 donut 圖表所需的 distribution 函數。

定義（複用 web.fleet.get_fleet_view 的語意）：
- online: connection 正常 + 無異常事件
- signal_lost: 訊號斷（cam 仍在但無影像）
- no_signal: 離線（cam disconnect）
- ghost: 已被 NVR 移除的 cam（永遠不計入 donut，但顯示在 legend）

設計：
- 修前 → 函數不存在（測試紅）
- 修後 → 回傳 dict 含 5 個 key
"""

from __future__ import annotations

import gc
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path


from db.sqlite_writer import SqliteWriter


@contextmanager
def _temp_db_with_cams(cam_specs: list[str]):
    """建立 NVR + cams，cam_specs 為 device_id 列表。

    為每台 cam 注入一個「即時事件」決定類別：
    - 'online' → 沒事件
    - 'signal_lost' → DEVICE_VIDEO_SIGNAL_LOST
    - 'no_signal' → STATE_DISCONNECTED
    - 'ghost_marked' → 標記後 (在 fixture 外做)
    - 'ghost' → 建立後立刻標記為 is_ghost=1
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr(
        {
            "id": "NVR-T",
            "name": "test nvr",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    rid = w.begin_scan_run("2026-07-31T00:00:00Z")

    # 建 cam（含 connection_state）
    cam_data = {}
    event_data = []
    for i, kind in enumerate(cam_specs, start=1):
        device_id = f"dev-{i}"
        if kind == "online":
            cam_data[device_id] = {"name": f"cam{i}", "connection_state": "CONNECTED"}
        elif kind in ("signal_lost", "no_signal"):
            cam_data[device_id] = {
                "name": f"cam{i}",
                "connection_state": "DISCONNECTED",
            }
            if kind == "signal_lost":
                event_data.append(
                    {
                        "eventId": f"e{i}",
                        "deviceId": device_id,
                        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
                        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
                        "occurred_at": "2026-07-31T00:00:00Z",
                    }
                )
            else:  # no_signal
                event_data.append(
                    {
                        "eventId": f"e{i}",
                        "deviceId": device_id,
                        "eventTopics": ["STATE_DISCONNECTED"],
                        "eventTopic": "STATE_DISCONNECTED",
                        "occurred_at": "2026-07-31T00:00:00Z",
                    }
                )
        elif kind == "ghost":
            cam_data[device_id] = {
                "name": f"rtsp://ghost{i}",
                "connection_state": "DISCONNECTED",
            }

    w.upsert_cameras(nvr_int, cam_data)
    if event_data:
        w.insert_events(rid, nvr_int, event_data)
    w.finish_scan_run(
        rid,
        finished_at="2026-07-31T00:01:00Z",
        status="complete",
        stats={
            "total_cameras": len(cam_specs),
            "abnormal_cameras": len(event_data),
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )
    w.close()

    # 標記 ghost
    ghost_device_ids = [f"dev-{i+1}" for i, k in enumerate(cam_specs) if k == "ghost"]
    if ghost_device_ids:
        conn = sqlite3.connect(db_path)
        for did in ghost_device_ids:
            conn.execute("UPDATE cameras SET is_ghost = 1 WHERE device_id = ?", (did,))
        conn.commit()
        conn.close()

    try:
        yield db_path
    finally:
        gc.collect()
        try:
            Path(db_path).unlink()
        except OSError:
            pass


# === 1. 基本：3 台 cam 各一類 ===
def test_health_distribution_basic():
    """1 online / 1 signal_lost / 1 no_signal → 1/1/1。"""
    with _temp_db_with_cams(["online", "signal_lost", "no_signal"]) as db_path:
        from web.fleet import get_camera_health_distribution

        d = get_camera_health_distribution(db_path)
        assert d["online"] == 1, d
        assert d["signal_lost"] == 1, d
        assert d["no_signal"] == 1, d
        assert d["total"] == 3, d
        assert d["ghost_count"] == 0, d


# === 2. Ghost cam 應不計入 donut，但 ghost_count 應反映 ===
def test_health_distribution_excludes_ghost():
    """1 online + 1 ghost → donut 1 台，ghost_count = 1。"""
    with _temp_db_with_cams(["online", "ghost"]) as db_path:
        from web.fleet import get_camera_health_distribution

        d = get_camera_health_distribution(db_path)
        assert d["online"] == 1, d
        assert d["total"] == 1, d
        assert d["ghost_count"] == 1, d


# === 3. 空 DB：全部 0 ===
def test_health_distribution_empty():
    """0 台 cam → 全 0。"""
    with _temp_db_with_cams([]) as db_path:
        from web.fleet import get_camera_health_distribution

        d = get_camera_health_distribution(db_path)
        assert d == {
            "online": 0,
            "signal_lost": 0,
            "no_signal": 0,
            "total": 0,
            "ghost_count": 0,
        }, d
