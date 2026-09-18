"""Tests for web/coverage.py — 純邏輯（不碰 Flask / DB）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from web.coverage import (
    parse_records_from_timeline_response,
    compute_per_camera_completeness,
    compute_axis_ticks,
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
                        {
                            "start": "2026-08-04T00:00:00Z",
                            "end": "2026-08-04T01:00:00Z",
                        },
                        {
                            "start": "2026-08-04T03:00:00Z",
                            "end": "2026-08-04T05:00:00Z",
                        },
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
    assert (
        compute_per_camera_completeness(
            [], "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z"
        )
        == 0.0
    )


def test_compute_per_camera_completeness_full_window_returns_one():
    """錄影完全覆蓋視窗 → 完整率 1.0。"""
    records = [("2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z")]
    assert (
        compute_per_camera_completeness(
            records, "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z"
        )
        == 1.0
    )


def test_compute_per_camera_completeness_half_window_returns_half():
    """半小時錄影 / 1 小時視窗 → 0.5。"""
    records = [("2026-08-04T00:00:00Z", "2026-08-04T00:30:00Z")]
    assert (
        compute_per_camera_completeness(
            records, "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z"
        )
        == 0.5
    )


# === fetch_coverage_from_nvr ===


def test_fetch_coverage_from_nvr_returns_dict_with_cameras_list():
    """fetch_coverage_from_nvr 回傳 dict 含 'cameras' list（每 cam 1 個 dict: cam_id, records, completeness）。"""
    from web.coverage import fetch_coverage_from_nvr

    fake_nvr = {
        "host": "127.0.0.1",
        "port": 8443,
        "nvr_id": "ACC8-P4",
        "user_nonce": "u",
        "user_key": "k",
    }
    fake_cameras = [
        {"device_id": "cam-001", "camera_name": "Cam 1"},
        {"device_id": "cam-002", "camera_name": "Cam 2"},
    ]
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-001",
                    "record": [
                        {"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"}
                    ],
                },
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
        "host": "127.0.0.1",
        "port": 8443,
        "nvr_id": "ACC8-P4",
        "user_nonce": "u",
        "user_key": "k",
    }
    fake_cameras = [{"device_id": "cam-001", "camera_name": "Cam 1"}]
    # NVR 回的 records 含視窗外（7/29 舊資料）+ 視窗內 1 段 + 視窗外 5:00~6:00
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-001",
                    "record": [
                        {
                            "start": "2026-07-29T18:30:00Z",
                            "end": "2026-07-29T20:00:00Z",
                        },
                        {
                            "start": "2026-08-04T00:10:00Z",
                            "end": "2026-08-04T00:20:00Z",
                        },
                        {
                            "start": "2026-08-04T05:00:00Z",
                            "end": "2026-08-04T06:00:00Z",
                        },
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
    assert (
        len(cam["records"]) == 1
    ), f"expected 1 record after clip, got {len(cam['records'])}: {cam['records']}"
    s, e = cam["records"][0]
    assert s == "2026-08-04T00:10:00+00:00", f"unexpected start: {s}"
    assert e == "2026-08-04T00:20:00+00:00", f"unexpected end: {e}"
    # 完整率：10 分鐘 / 60 分鐘 ≈ 0.1667
    assert cam["completeness"] == pytest.approx(10 / 60, rel=1e-3)


def test_fetch_coverage_from_nvr_clips_partially_overlapping_records():
    """視窗邊界的 record 一半在視窗外 → 裁切後只留視窗內部分。"""
    fake_nvr = {
        "host": "127.0.0.1",
        "port": 8443,
        "nvr_id": "X",
        "user_nonce": "u",
        "user_key": "k",
    }
    fake_cameras = [{"device_id": "cam-A", "camera_name": "A"}]
    # 查 00:30~01:30，這條 record 從 00:00~01:00 → 裁切後 00:30~01:00
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-A",
                    "record": [
                        {"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"}
                    ],
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
    fake_nvr = {
        "host": "127.0.0.1",
        "port": 8443,
        "nvr_id": "X",
        "user_nonce": "u",
        "user_key": "k",
    }
    fake_cameras = [{"device_id": "cam-B", "camera_name": "B"}]
    fake_raw = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-B",
                    # 兩條都在視窗外
                    "record": [
                        {
                            "start": "2026-07-29T18:30:00Z",
                            "end": "2026-07-29T20:00:00Z",
                        },
                        {
                            "start": "2026-08-05T00:00:00Z",
                            "end": "2026-08-05T01:00:00Z",
                        },
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


# === 24h 軸動態化（user 031.PNG 回饋：軸寫死 00-22 不對應實際視窗） ===


def test_compute_axis_ticks_returns_12_by_default():
    """預設 12 個 tick 從 0% 到 100% 等距分佈。"""
    ticks = compute_axis_ticks("2026-08-03T15:52:00Z", "2026-08-04T15:52:00Z")
    assert len(ticks) == 12
    assert ticks[0]["position_pct"] == 0.0
    assert ticks[-1]["position_pct"] == 100.0
    # 等距（每個相差約 100/11 ≈ 9.09）
    diffs = [
        ticks[i + 1]["position_pct"] - ticks[i]["position_pct"]
        for i in range(len(ticks) - 1)
    ]
    for d in diffs:
        assert abs(d - 100 / 11) < 0.01


def test_compute_axis_ticks_labels_are_taipei_hh_mm():
    """label 應為台北時間 HH:MM（start_iso/ end_iso 是 UTC，函式內部轉 +08:00）。
    UTC 2026-08-03T15:52 = 台北 2026-08-03 23:52、UTC 8/4T15:52 = 台北 8/4 23:52。
    跨日視窗 → label 顯示 MM-DD HH:MM。
    """
    ticks = compute_axis_ticks("2026-08-03T15:52:00Z", "2026-08-04T15:52:00Z")
    assert ticks[0]["label"] == "08-03 23:52"
    assert ticks[-1]["label"] == "08-04 23:52"
    import re

    for t in ticks:
        assert re.match(
            r"^\d{2}-\d{2} \d{2}:\d{2}$", t["label"]
        ), f"bad label format: {t['label']}"


def test_compute_axis_ticks_custom_num():
    """可自訂 tick 數（例 6 個）。UTC 8/4T00:00 = 台北 8/4T08:00，
    end UTC 8/4T06:00 = 台北 8/4T14:00。start/end 同日 → label 只顯示 HH:MM。"""
    ticks = compute_axis_ticks(
        "2026-08-04T00:00:00Z", "2026-08-04T06:00:00Z", num_ticks=6
    )
    assert len(ticks) == 6
    assert ticks[0]["label"] == "08:00"
    assert ticks[-1]["label"] == "14:00"


def test_compute_axis_ticks_cross_midnight_shows_day_change():
    """跨日視窗（台北時間）：label 應顯示日期變化。
    UTC 2026-08-03T12:00:00Z = 台北 8/3 20:00、UTC 8/4T08:00:00Z = 台北 8/4 16:00。
    所以 ticks[0] 應在 8/3、ticks[-1] 在 8/4。
    """
    ticks = compute_axis_ticks(
        "2026-08-03T12:00:00Z", "2026-08-04T08:00:00Z", num_ticks=7
    )
    assert ticks[0]["label"].startswith("08-03 ")
    assert ticks[-1]["label"].startswith("08-04 ")


def test_compute_axis_ticks_short_window_less_than_2h():
    """視窗 < 2 小時仍應回傳合理 tick（避免除以 0）。"""
    ticks = compute_axis_ticks("2026-08-04T00:00:00Z", "2026-08-04T00:30:00Z")
    # 應該至少有 2 個 tick（首尾）
    assert len(ticks) >= 2
    assert ticks[0]["position_pct"] == 0.0
    assert ticks[-1]["position_pct"] == 100.0


# === fetch_coverage_from_nvr 包含 axis_ticks ===


def test_fetch_coverage_from_nvr_includes_axis_ticks():
    """fetch_coverage_from_nvr 回傳應含 axis_ticks 欄位（給前端 render 軸用）。"""
    fake_nvr = {
        "host": "127.0.0.1",
        "port": 8443,
        "nvr_id": "X",
        "user_nonce": "u",
        "user_key": "k",
    }
    fake_cameras = [{"device_id": "cam-001", "camera_name": "Cam 1"}]
    fake_raw = {"result": {"timelines": [{"cameraId": "cam-001", "record": []}]}}
    out = fetch_coverage_from_nvr(
        nvr=fake_nvr,
        cameras=fake_cameras,
        start_iso="2026-08-03T15:52:00Z",
        end_iso="2026-08-04T15:52:00Z",
        timeline_fetcher=lambda *a, **k: fake_raw,
    )
    assert "axis_ticks" in out
    assert len(out["axis_ticks"]) == 12
    # UTC 15:52 = 台北 23:52
    assert out["axis_ticks"][0]["label"] == "08-03 23:52"
    assert out["axis_ticks"][-1]["label"] == "08-04 23:52"
