"""Tests for web/coverage.py — 純邏輯（不碰 Flask / DB）。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from web.coverage import (
    parse_records_from_timeline_response,
    compute_per_camera_completeness,
    fetch_coverage_from_nvr,
    CoverageCamera,
)


# === parse_records_from_timeline_response ===

def test_parse_records_from_timeline_response_empty_returns_empty():
    """空 payload → 回空 dict。"""
    assert parse_records_from_timeline_response(None) == {}
    assert parse_records_from_timeline_response({}) == {}
    assert parse_records_from_timeline_response({"result": {"timelines": []}}) == {}


def test_parse_records_from_timeline_response_one_camera_two_records():
    """1 台 cam、2 段錄影 → 回 1 個 key、2 個 (start, end) tuple。"""
    payload = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-001",
                    "record": [
                        {"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"},
                        {"start": "2026-08-04T03:00:00Z", "end": "2026-08-04T05:00:00Z"},
                    ],
                }
            ]
        }
    }
    out = parse_records_from_timeline_response(payload)
    assert "cam-001" in out
    assert len(out["cam-001"]) == 2
    s1, e1 = out["cam-001"][0]
    assert s1 == datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)
    assert e1 == datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)


# === compute_per_camera_completeness ===

def test_compute_per_camera_completeness_empty_window_returns_zero():
    """空錄影 → 完整率 0.0。"""
    assert compute_per_camera_completeness([], "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z") == 0.0


def test_compute_per_camera_completeness_full_window_returns_one():
    """錄影完全覆蓋視窗 → 完整率 1.0。"""
    records = [("2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z")]
    assert compute_per_camera_completeness(records, "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z") == 1.0


def test_compute_per_camera_completeness_half_window_returns_half():
    """半小時錄影 / 1 小時視窗 → 0.5。"""
    records = [("2026-08-04T00:00:00Z", "2026-08-04T00:30:00Z")]
    assert compute_per_camera_completeness(records, "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z") == 0.5


# === fetch_coverage_from_nvr ===

def test_fetch_coverage_from_nvr_returns_dict_with_cameras_list():
    """fetch_coverage_from_nvr 回傳 dict 含 'cameras' list（每 cam 1 個 dict: cam_id, records, completeness）。"""
    from web.coverage import fetch_coverage_from_nvr

    fake_nvr = {
        "host": "127.0.0.1", "port": 8443, "nvr_id": "ACC8-P4",
        "user_nonce": "u", "user_key": "k",
    }
    fake_cameras = [
        {"device_id": "cam-001", "camera_name": "Cam 1"},
        {"device_id": "cam-002", "camera_name": "Cam 2"},
    ]
    fake_raw = {
        "result": {
            "timelines": [
                {"cameraId": "cam-001", "record": [{"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"}]},
                {"cameraId": "cam-002", "record": []},
            ]
        }
    }
    out = fetch_coverage_from_nvr(
        nvr=fake_nvr,
        cameras=fake_cameras,
        start_iso="2026-08-04T00:00:00Z",
        end_iso="2026-08-04T01:00:00Z",
        timeline_fetcher=lambda *args, **kwargs: fake_raw,
    )
    assert "cameras" in out
    assert len(out["cameras"]) == 2
    cam1 = next(c for c in out["cameras"] if c["cam_id"] == "cam-001")
    assert cam1["completeness"] == 1.0
    assert len(cam1["records"]) == 1
    cam2 = next(c for c in out["cameras"] if c["cam_id"] == "cam-002")
    assert cam2["completeness"] == 0.0


def test_coverage_camera_dataclass_fields():
    """CoverageCamera 至少含 cam_id / camera_name / records / completeness 欄位。"""
    from dataclasses import fields as dc_fields
    field_names = {f.name for f in dc_fields(CoverageCamera)}
    assert {"cam_id", "camera_name", "records", "completeness"}.issubset(field_names)
