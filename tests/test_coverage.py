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


# === NVR /timeline 範圍查詢 bug 修法 ===

def test_fetch_coverage_from_nvr_clips_records_to_window():
    """NVR /timeline 會忽略 from/to 範圍，回傳視窗外的 records。
    fetch_coverage_from_nvr 必須把 records 裁切到 [start, end] 視窗內再序列化，
    否則前端會誤繪視窗外的綠帶。
    """
    fake_nvr = {
        "host": "127.0.0.1", "port": 8443, "nvr_id": "ACC8-P4",
        "user_nonce": "u", "user_key": "k",
    }
    fake_cameras = [{"device_id": "cam-001", "camera_name": "Cam 1"}]
    # NVR 回的 records 含視窗外（7/29 舊資料）+ 視窗內 1 段 + 視窗外 5:00~6:00
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-001",
                    "record": [
                        {"start": "2026-07-29T18:30:00Z", "end": "2026-07-29T20:00:00Z"},
                        {"start": "2026-08-04T00:10:00Z", "end": "2026-08-04T00:20:00Z"},
                        {"start": "2026-08-04T05:00:00Z", "end": "2026-08-04T06:00:00Z"},
                    ],
                }
            ]
        }
    }
    # 查 8/4 00:00 ~ 01:00（1 小時視窗）
    out = fetch_coverage_from_nvr(
        nvr=fake_nvr,
        cameras=fake_cameras,
        start_iso="2026-08-04T00:00:00Z",
        end_iso="2026-08-04T01:00:00Z",
        timeline_fetcher=lambda *args, **kwargs: fake_raw,
    )
    cam = next(c for c in out["cameras"] if c["cam_id"] == "cam-001")
    # 應該只剩視窗內那 1 段（10 分鐘）
    assert len(cam["records"]) == 1, f"expected 1 record after clip, got {len(cam['records'])}: {cam['records']}"
    s, e = cam["records"][0]
    assert s == "2026-08-04T00:10:00+00:00", f"unexpected start: {s}"
    assert e == "2026-08-04T00:20:00+00:00", f"unexpected end: {e}"
    # 完整率：10 分鐘 / 60 分鐘 ≈ 0.1667
    assert cam["completeness"] == pytest.approx(10 / 60, rel=1e-3)


def test_fetch_coverage_from_nvr_clips_partially_overlapping_records():
    """視窗邊界的 record 一半在視窗外 → 裁切後只留視窗內部分。"""
    fake_nvr = {"host": "127.0.0.1", "port": 8443, "nvr_id": "X", "user_nonce": "u", "user_key": "k"}
    fake_cameras = [{"device_id": "cam-A", "camera_name": "A"}]
    # 查 00:30~01:30，這條 record 從 00:00~01:00 → 裁切後 00:30~01:00
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-A",
                    "record": [{"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"}],
                }
            ]
        }
    }
    out = fetch_coverage_from_nvr(
        nvr=fake_nvr,
        cameras=fake_cameras,
        start_iso="2026-08-04T00:30:00Z",
        end_iso="2026-08-04T01:30:00Z",
        timeline_fetcher=lambda *args, **kwargs: fake_raw,
    )
    cam = next(c for c in out["cameras"] if c["cam_id"] == "cam-A")
    assert len(cam["records"]) == 1
    s, e = cam["records"][0]
    assert s == "2026-08-04T00:30:00+00:00"
    assert e == "2026-08-04T01:00:00+00:00"
    # 完整率：30 分鐘 / 60 分鐘 = 0.5
    assert cam["completeness"] == pytest.approx(0.5, rel=1e-3)


def test_fetch_coverage_from_nvr_drops_records_completely_outside_window():
    """完全在視窗外的 record → 整條 drop，不出現在序列化結果。"""
    fake_nvr = {"host": "127.0.0.1", "port": 8443, "nvr_id": "X", "user_nonce": "u", "user_key": "k"}
    fake_cameras = [{"device_id": "cam-B", "camera_name": "B"}]
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-B",
                    # 兩條都在視窗外
                    "record": [
                        {"start": "2026-07-29T18:30:00Z", "end": "2026-07-29T20:00:00Z"},
                        {"start": "2026-08-05T00:00:00Z", "end": "2026-08-05T01:00:00Z"},
                    ],
                }
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
    cam = next(c for c in out["cameras"] if c["cam_id"] == "cam-B")
    assert cam["records"] == [], f"expected empty records, got {cam['records']}"
    assert cam["completeness"] == 0.0
