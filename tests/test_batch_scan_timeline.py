"""
tests/test_batch_scan_timeline.py
=================================
batch_scan 的 timeline 整合測試。

驗證：
- 啟用 NVR_TIMELINE=1 時，batch_scan 跑完 scanner.scan() 後會用 scanner.get_timeline()
  抓每台 cam 的 24h timeline → 計算完整率 → 寫入 recording_status
- 沒 NVR_TIMELINE=1 → 跳過

純函式輔助（_summarize_timeline）：寫在 batch_scan.py 內，
驗證其從 NVR 回傳 dict 算出 per-cam (completeness, missing_seconds)。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.sqlite_writer import SqliteWriter


def _wrap(result):
    return MagicMock(
        status_code=200,
        ok=True,
        json=lambda: {"status": "success", "result": result},
        text="...",
    )


# === 純函式 _summarize_timeline 的測試 ===


def _import_helper():
    """動態導入避免 module-level 副作用。"""
    from batch_scan import _summarize_timeline

    return _summarize_timeline


def test_summarize_timeline_full_coverage():
    """24h 完整涵蓋 → completeness=1.0, missing=0。"""
    from datetime import datetime, timedelta, timezone
    from web.timeline import parse_timeline_response

    _summarize_timeline = _import_helper()
    start = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    raw = {
        "timelines": [
            {
                "cameraId": "c1",
                "record": [
                    {"start": "2026-07-28T00:00:00Z", "end": "2026-07-29T00:00:00Z"},
                ],
            }
        ]
    }
    parsed = parse_timeline_response(raw)
    summaries = _summarize_timeline(parsed, start, end)
    assert len(summaries) == 1
    assert summaries[0]["camera_id"] == "c1"
    assert summaries[0]["completeness"] == pytest.approx(1.0)
    assert summaries[0]["missing_seconds"] == pytest.approx(0.0)


def test_summarize_timeline_empty_records():
    """沒任何 record → completeness=0。"""
    from datetime import datetime, timedelta, timezone
    from web.timeline import parse_timeline_response

    _summarize_timeline = _import_helper()
    start = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    raw = {"timelines": [{"cameraId": "c1", "record": []}]}
    parsed = parse_timeline_response(raw)
    summaries = _summarize_timeline(parsed, start, end)
    assert summaries[0]["completeness"] == 0.0
    assert summaries[0]["missing_seconds"] == pytest.approx(86400.0)


# === _timeline_check_loop 整合：寫入 recording_status ===


def test_timeline_check_loop_writes_recording_status():
    """_timeline_check_loop 應把每台 cam 的完整率寫入 recording_status 表。"""
    from batch_scan import _timeline_check_loop
    from datetime import datetime, timedelta, timezone

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        w = SqliteWriter(db_path)
        nvra = w.upsert_nvr(
            {
                "id": "NVR-A",
                "name": "A",
                "host": "10.0.0.1",
                "port": 8443,
                "username": "u",
                "password": "p",
            }
        )
        rid = w.begin_scan_run("2026-07-29T00:00:00Z")
        w.upsert_cameras(
            nvra,
            {
                "c1": {"name": "cam1", "connection_state": "CONNECTED"},
            },
        )

        # mock scanner.get_timeline 回傳 24h 完整涵蓋
        scanner = MagicMock()
        now = datetime.now(timezone.utc)
        from_iso = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        scanner.get_timeline.return_value = {
            "timelines": [
                {
                    "cameraId": "c1",
                    "record": [
                        {"start": from_iso, "end": to_iso},
                    ],
                }
            ],
        }

        summary = _timeline_check_loop(scanner, nvra, w, verbose=False)
        assert summary["checked"] == 1
        assert summary["written"] == 1
        assert summary["errors"] == []

        # 驗證 DB 寫入
        row = (
            w._get_conn()
            .execute(
                "SELECT camera_id, completeness, missing_seconds FROM recording_status"
            )
            .fetchone()
        )
        assert row["camera_id"] == "c1"
        # 視窗以 datetime.now() 計算 → 與 mock 的 from/to 差幾秒，
        # 完整率非常接近 1 但不嚴格等於 1。用寬鬆比較。
        assert row["completeness"] > 0.99
        assert row["missing_seconds"] < 100  # 視窗漂移最多幾十秒
        w.close()
        gc.collect()
    finally:
        try:
            Path(db_path).unlink()
        except OSError:
            pass


def test_timeline_check_loop_handles_api_error_gracefully():
    """單台 cam 失敗不影響其他 cam。"""
    from batch_scan import _timeline_check_loop

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        w = SqliteWriter(db_path)
        nvra = w.upsert_nvr(
            {
                "id": "N",
                "name": "N",
                "host": "1.1.1.1",
                "port": 8443,
                "username": "u",
                "password": "p",
            }
        )
        rid = w.begin_scan_run("2026-07-29T00:00:00Z")
        w.upsert_cameras(
            nvra,
            {
                "c1": {"name": "cam1", "connection_state": "CONNECTED"},
                "c2": {"name": "cam2", "connection_state": "CONNECTED"},
            },
        )

        scanner = MagicMock()
        # 第一次拋例外，第二次回空
        scanner.get_timeline.side_effect = [
            Exception("API timeout"),
            {"timelines": [{"cameraId": "c2", "record": []}]},
        ]

        summary = _timeline_check_loop(scanner, nvra, w, verbose=False)
        assert summary["checked"] == 2  # 兩台都嘗試
        assert summary["written"] == 1  # c2 寫入 1 筆（completeness=0）
        assert len(summary["errors"]) == 1
        assert "c1" in summary["errors"][0]
        w.close()
        gc.collect()
    finally:
        try:
            Path(db_path).unlink()
        except OSError:
            pass
