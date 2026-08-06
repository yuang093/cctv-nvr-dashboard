"""Seed sample DB for visual verification of Spec G /trends deep-link flow.

灌 2 台 NVR + 6 台 cam + 24h 混合健康狀態的 image_health_checks，
給 chrome-devtools 截圖用。
"""
from __future__ import annotations
import json as _json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_offset(hours_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            host TEXT NOT NULL DEFAULT '',
            port INTEGER NOT NULL DEFAULT 8443,
            username TEXT, password TEXT,
            verify_ssl INTEGER NOT NULL DEFAULT 0,
            site_id TEXT, tags TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT '2026-08-05T00:00:00Z'
        );
        CREATE TABLE IF NOT EXISTS cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            is_ghost INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS image_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            nvr_server_id INTEGER,
            checked_at_utc TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            flags_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recording_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL,
            camera_id TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            completeness REAL NOT NULL,
            missing_seconds REAL NOT NULL DEFAULT 0,
            checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            total_nvrs INTEGER DEFAULT 0,
            ok_nvrs INTEGER DEFAULT 0,
            failed_nvrs INTEGER DEFAULT 0,
            total_cameras INTEGER DEFAULT 0,
            abnormal_cameras INTEGER DEFAULT 0,
            error_message TEXT DEFAULT ''
        );
    """)

    # 2 台 NVR
    nvr_a = conn.execute(
        "INSERT INTO nvr_servers (nvr_id, name, host) VALUES ('branch-a', 'ACC-8 大樓', '192.168.1.10')"
    ).lastrowid
    nvr_b = conn.execute(
        "INSERT INTO nvr_servers (nvr_id, name, host) VALUES ('branch-b', 'ACC-9 分店', '192.168.1.11')"
    ).lastrowid

    # 6 台 cam（含一 ghost 應過濾）
    cams = [
        (nvr_a, "lobby-main", "大門主攝", False),
        (nvr_a, "lobby-side", "大門側攝", False),
        (nvr_a, "parking-1", "停車場 1", False),
        (nvr_b, "cashier-1", "收銀台 1", False),
        (nvr_b, "warehouse", "倉庫", False),
        (nvr_a, "ghost-rtsp", "rtsp://legacy/old", True),
    ]
    for nvr_id, dev_id, name, ghost in cams:
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nvr_id, dev_id, name, 1 if ghost else 0, _now_iso()),
        )

    # image_health_checks：分散 24h，每 bin 4 筆（部分 frozen/underexposed 模擬異常）
    cam_health_seed = [
        # (device_id, [(hours_ago, is_frozen, is_underexposed), ...])
        ("lobby-main", [
            # 健康：8 筆全 healthy
            *[(h, False, False) for h in [1, 3, 5, 7, 9, 11, 13, 15]],
            # 早期 frozen spike（18-20h ago）3 筆
            (18.2, True, False), (19.1, True, False), (20.0, True, False),
        ]),
        ("lobby-side", [
            # 健康：6 筆
            *[(h, False, False) for h in [2, 4, 6, 8, 10, 12]],
        ]),
        ("parking-1", [
            # 5-7h ago underexposed 3 筆（暗）
            (5.2, False, True), (6.1, False, True), (7.0, False, True),
            # 其他時間健康
            *[(h, False, False) for h in [9, 11, 13, 15, 17]],
        ]),
        ("cashier-1", [
            # 完全離線 8-12h ago（無 records）
            *[(h, False, False) for h in [1, 3, 5, 14, 16, 18, 20]],
        ]),
        ("warehouse", [
            # 全健康
            *[(h, False, False) for h in [2, 4, 6, 8, 10, 12, 14, 16]],
        ]),
    ]

    for dev_id, records in cam_health_seed:
        for h_ago, is_frozen, is_underexposed in records:
            metrics = {
                "blur_var": 100.0,
                "mean_luma": 0.3 if is_underexposed else 0.5,
                "is_blurry": False,
                "is_overexposed": False,
                "is_underexposed": is_underexposed,
                "frozen_diff": 0.0 if is_frozen else 0.5,
                "is_frozen": is_frozen,
            }
            conn.execute(
                "INSERT INTO image_health_checks "
                "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
                "VALUES (?, NULL, ?, ?, '[]')",
                (dev_id, _iso_offset(h_ago), _json.dumps(metrics)),
            )

    # recording_status：dashboard top_missing 用
    recording = [
        (nvr_a, "lobby-main", 0.92, 6912.0),    # 健康
        (nvr_a, "lobby-side", 0.88, 10368.0),
        (nvr_a, "parking-1", 0.45, 47520.0),    # 不良
        (nvr_b, "cashier-1", 0.30, 60480.0),    # 嚴重不良
        (nvr_b, "warehouse", 0.95, 4320.0),
    ]
    for nvr_id, dev_id, comp, miss_sec in recording:
        conn.execute(
            "INSERT INTO recording_status "
            "(nvr_id, camera_id, window_start, window_end, completeness, missing_seconds, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                nvr_id, dev_id,
                _iso_offset(24), _now_iso(),
                comp, miss_sec, _now_iso(),
            ),
        )

    # scan_runs：dashboard「最近 5 次掃描」用
    for i in range(5):
        status = "success" if i % 2 == 0 else "partial"
        started = _iso_offset(i * 4 + 0.5)
        finished = _iso_offset(i * 4)
        conn.execute(
            "INSERT INTO scan_runs "
            "(nvr_id, started_at, finished_at, status, total_nvrs, ok_nvrs, "
            " total_cameras, abnormal_cameras) "
            "VALUES (NULL, ?, ?, ?, 2, ?, 5, ?)",
            (started, finished, status, 2 if status == "success" else 1, i % 3),
        )

    conn.commit()
    conn.close()
    print(f"Seeded {db_path}: 2 NVR + 6 cams + image_health_checks + recording_status + scan_runs")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else ":memory:"
    seed(db_path)
