"""
tests/integration/test_e2e_resolved_tracking.py
================================================
Phase 1：事件 resolved 追蹤 — 端到端測試。

流程：
    scan_1: mock NVR 有 1 個異常相機 → events 表新增 1 筆（resolved_at=NULL）
    scan_2: 同一 NVR 這次所有相機都正常 → events 表那筆 resolved_at 被填入

驗證重點：
    - resolved_at 確實在第二次 scan 後被更新
    - 同一 NVR 但不同 device 不互相影響
    - 仍異常的 device 不會被 mark
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.sqlite_writer import SqliteWriter
from tests.integration.mock_acc import (
    MockAvigilonConfig,
    MockAvigilonServer,
)


def _one_cam_nvr_server(cameras: list[dict], events: list[dict] | None = None):
    """建一個簡化的 mock NVR（單台、用自訂 cameras / events）。"""
    cfg = MockAvigilonConfig(
        server_id=f"mock-{datetime.now().timestamp()}",
        cameras=cameras,
        events=events or [],
    )
    server = MockAvigilonServer(cfg)
    server.start()
    return server


# === fixtures ===
@pytest.fixture
def flush_urllib3_warnings():
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    yield


@pytest.fixture
def integration_writer(tmp_path):
    """檔案式 SQLite DB（測跨 transaction 用）。"""
    db_path = str(tmp_path / "resolved.db")
    yield SqliteWriter(db_path)


# === 1. 最基本的 happy path：第一次異常 → 第二次正常 → resolved ===
def test_first_abnormal_then_normal_marks_resolved(
    integration_writer, flush_urllib3_warnings,
):
    """
    scan_1: cam-002 後門 offline → events 寫入
    scan_2: cam-002 恢復 → 同一 NVR 連線一次，mark_resolved 把 scan_1 那筆 resolved
    """
    w = integration_writer
    nvr_int = w.upsert_nvr({
        "id": "nvr-r1", "name": "TestNVR", "host": "0.0.0.0",
        "username": "u", "password": "p",
    })

    # --- scan_1: 有 1 個異常相機 ---
    abnormal_cameras = [
        {
            "deviceId": "cam-002",
            "name": "後門",
            "logicalId": "L2",
            "connectionStatus": {"state": "LONG_FAILED"},
            "available": False,
        },
    ]
    abnormal_events = [{
        "eventId": "evt-r1",
        "deviceId": "cam-002",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "occurredAt": "2026-06-30T08:00:00Z",
    }]
    nvr_config = {
        "id": "nvr-r1", "name": "TestNVR", "host": "0.0.0.0", "port": 8443,
        "username": "u", "password": "p", "verify_ssl": False, "enabled": True,
    }

    with _one_cam_nvr_server(abnormal_cameras, abnormal_events) as srv1:
        from nvr_scanner import AvigilonScanner
        r1 = w.begin_scan_run("2026-06-30T08:00:00Z")
        scanner = AvigilonScanner(
            {**nvr_config, "host": srv1.host, "port": srv1.port},
            user_nonce="n", user_key="k", integration_id="",
        )
        result1 = scanner.scan()
        w.upsert_cameras(nvr_int, result1["cameras"])
        w.insert_events(r1, nvr_int, result1["events"])
        w.mark_resolved(r1, nvr_int)
        w.finish_scan_run(
            r1, finished_at="2026-06-30T08:01:00Z",
            status="success",
            stats={"total_cameras": 1, "abnormal_cameras": 1},
        )

    # 中：scan_1 那筆還是 OPEN
    row = w._conn.execute(
        "SELECT resolved_at FROM events WHERE event_id='evt-r1'",
    ).fetchone()
    assert row["resolved_at"] is None, "scan_1 之後仍是 OPEN"

    # --- scan_2: cam-002 恢復（不 insert 任何 event）---
    normal_cameras = [
        {
            "deviceId": "cam-002",
            "name": "後門",
            "logicalId": "L2",
            "connectionStatus": {"state": "CONNECTED"},
            "available": True,
        },
    ]
    with _one_cam_nvr_server(normal_cameras, []) as srv2:
        from nvr_scanner import AvigilonScanner
        r2 = w.begin_scan_run("2026-06-30T09:00:00Z")
        scanner2 = AvigilonScanner(
            {**nvr_config, "host": srv2.host, "port": srv2.port},
            user_nonce="n", user_key="k", integration_id="",
        )
        result2 = scanner2.scan()
        w.upsert_cameras(nvr_int, result2["cameras"])
        w.insert_events(r2, nvr_int, result2["events"])
        # 沒有異常事件 → result2["events"] 為空 list
        assert result2["events"] == []
        count = w.mark_resolved(r2, nvr_int)
        w.finish_scan_run(
            r2, finished_at="2026-06-30T09:01:00Z",
            status="success",
            stats={"total_cameras": 1, "abnormal_cameras": 0},
        )

    assert count == 1, "scan_2 應該把 scan_1 那筆 d1 標記為 resolved"

    # 最終：scan_1 那筆 resolved_at 被填入
    row = w._conn.execute(
        "SELECT resolved_at FROM events WHERE event_id='evt-r1'",
    ).fetchone()
    assert row["resolved_at"] is not None, (
        "第二次 scan 後，scan_1 的事件應該已被 mark_resolved"
    )
    today_prefix = datetime.utcnow().strftime("%Y-%m-%dT")
    assert row["resolved_at"].startswith(today_prefix), (
        f"resolved_at 應為當下 ISO，got {row['resolved_at']!r}"
    )


# === 2. 同一 NVR 跨 scan_run 仍有異常的不會被 mark ===
def test_still_abnormal_across_scans_not_resolved(
    integration_writer, flush_urllib3_warnings,
):
    """scan_1 + scan_2 cam-002 都異常 → resolved_at 仍是 NULL。"""
    w = integration_writer
    nvr_int = w.upsert_nvr({
        "id": "nvr-r2", "name": "TestNVR2", "host": "0.0.0.0",
        "username": "u", "password": "p",
    })
    nvr_config = {
        "id": "nvr-r2", "name": "TestNVR2", "host": "0.0.0.0", "port": 8443,
        "username": "u", "password": "p", "verify_ssl": False, "enabled": True,
    }
    abnormal_cameras = [
        {
            "deviceId": "cam-stuck",
            "name": "卡住的相機",
            "logicalId": "L1",
            "connectionStatus": {"state": "DISCONNECTED"},
            "available": False,
        },
    ]
    abnormal_events_1 = [{
        "eventId": "evt-stuck-1", "deviceId": "cam-stuck",
        "eventTopics": ["DEVICE_DISCONNECTED"],
        "occurredAt": "2026-06-30T08:00:00Z",
    }]
    abnormal_events_2 = [{
        "eventId": "evt-stuck-2", "deviceId": "cam-stuck",
        "eventTopics": ["DEVICE_DISCONNECTED"],
        "occurredAt": "2026-06-30T09:00:00Z",
    }]

    # scan_1
    with _one_cam_nvr_server(abnormal_cameras, abnormal_events_1) as srv1:
        from nvr_scanner import AvigilonScanner
        r1 = w.begin_scan_run("2026-06-30T08:00:00Z")
        scanner = AvigilonScanner(
            {**nvr_config, "host": srv1.host, "port": srv1.port},
            user_nonce="n", user_key="k", integration_id="",
        )
        result = scanner.scan()
        w.upsert_cameras(nvr_int, result["cameras"])
        w.insert_events(r1, nvr_int, result["events"])
        w.mark_resolved(r1, nvr_int)
        w.finish_scan_run(
            r1, finished_at="2026-06-30T08:01:00Z", status="success",
            stats={"total_cameras": 1, "abnormal_cameras": 1},
        )

    # scan_2: 仍異常
    with _one_cam_nvr_server(abnormal_cameras, abnormal_events_2) as srv2:
        from nvr_scanner import AvigilonScanner
        r2 = w.begin_scan_run("2026-06-30T09:00:00Z")
        scanner = AvigilonScanner(
            {**nvr_config, "host": srv2.host, "port": srv2.port},
            user_nonce="n", user_key="k", integration_id="",
        )
        result = scanner.scan()
        w.upsert_cameras(nvr_int, result["cameras"])
        w.insert_events(r2, nvr_int, result["events"])
        count = w.mark_resolved(r2, nvr_int)
        w.finish_scan_run(
            r2, finished_at="2026-06-30T09:01:00Z", status="success",
            stats={"total_cameras": 1, "abnormal_cameras": 1},
        )

    assert count == 0, "cam-stuck 仍異常，掃描_2 不該 mark"

    rows = w._conn.execute(
        "SELECT event_id, resolved_at FROM events ORDER BY id"
    ).fetchall()
    by_id = {r["event_id"]: r["resolved_at"] for r in rows}
    # 去重邏輯生效後，scan_2 不會再 insert 新 event；
    # 唯一的事件是 scan_1 的 evt-stuck-1，且仍 OPEN
    assert by_id["evt-stuck-1"] is None
    assert "evt-stuck-2" not in by_id  # 確認去重生效（沒新 row）


# === 3. mark_resolved 不動已 resolved 的事件（idempotent） ===
def test_resolved_events_not_overwritten(
    integration_writer, flush_urllib3_warnings,
):
    """已 resolved 的事件，再次跑 mark_resolved 不會被覆寫。"""
    w = integration_writer
    nvr_int = w.upsert_nvr({
        "id": "nvr-r3", "name": "TestNVR3", "host": "0.0.0.0",
        "username": "u", "password": "p",
    })

    # 手動灌一筆已 resolved 的事件
    r1 = w.begin_scan_run("2026-06-30T08:00:00Z")
    w._conn.execute(
        """
        INSERT INTO events (
            scan_run_id, nvr_id, event_id, device_id,
            event_topic, event_topics_json, occurred_at,
            detected_at, resolved_at, raw_json
        ) VALUES (?, ?, 'old-resolved', 'd1', 'X', '["X"]',
                  '2026-06-30T08:00:00Z', '2026-06-30T08:00:00Z',
                  '2026-06-30T08:30:00Z', '{}')
        """,
        (r1, nvr_int),
    )
    w.finish_scan_run(
        r1, finished_at="2026-06-30T08:01:00Z", status="success",
        stats={"total_cameras": 1, "abnormal_cameras": 0},
    )

    # scan_2 沒新 event
    r2 = w.begin_scan_run("2026-06-30T09:00:00Z")
    count = w.mark_resolved(r2, nvr_int)
    assert count == 0, "已 resolved 的不該被算進去"

    # 原 resolved_at 沒被覆寫
    row = w._conn.execute(
        "SELECT resolved_at FROM events WHERE event_id='old-resolved'",
    ).fetchone()
    assert row["resolved_at"] == "2026-06-30T08:30:00Z", "原本的 resolved 時間應保留"
