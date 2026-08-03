"""
tests/test_timeline.py
======================
錄影時間軸（/timeline）的純函式單元測試。

測試範圍：
- `parse_timeline_response(data)` — 從 NVR 原始 JSON 抽出 {camera_id: [(start, end), ...]}
- `compute_completeness(records, window_start, window_end)` — 算 0.0-1.0 完整率
- `compute_missing_segments(records, window_start, window_end)` — 缺口清單

這些函式純資料處理，不碰 NVR / DB / Flask — 純單元測試。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from web.timeline import (
    parse_timeline_response,
    compute_completeness,
    compute_missing_segments,
)


# === parse_timeline_response ===

def test_parse_timeline_response_returns_empty_for_empty_data():
    """空輸入應回空 dict。"""
    assert parse_timeline_response({}) == {}
    assert parse_timeline_response(None) == {}


def test_parse_timeline_response_extracts_records_per_camera():
    """從 NVR 典型回應抽出 {camera_id: [(start, end), ...]}。"""
    data = {
        "timelines": [
            {
                "cameraId": "cam-1",
                "record": [
                    {"start": "2026-07-29T00:00:00Z", "end": "2026-07-29T01:00:00Z"},
                    {"start": "2026-07-29T01:00:00Z", "end": "2026-07-29T02:00:00Z"},
                ],
            },
            {
                "cameraId": "cam-2",
                "record": [],  # 沒錄
            },
        ]
    }
    result = parse_timeline_response(data)
    assert "cam-1" in result
    assert len(result["cam-1"]) == 2
    assert result["cam-1"][0] == (datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc),
                                  datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc))
    assert result["cam-2"] == []


def test_parse_timeline_response_handles_results_wrapper():
    """NVR 也可能用 `result.timelines` 包裹。"""
    data = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-1",
                    "record": [
                        {"start": "2026-07-29T00:00:00Z", "end": "2026-07-29T01:00:00Z"},
                    ],
                },
            ]
        }
    }
    result = parse_timeline_response(data)
    assert "cam-1" in result
    assert len(result["cam-1"]) == 1


def test_parse_timeline_response_skips_malformed_records():
    """缺 start/end 的 record 應跳過，不爆。"""
    data = {
        "timelines": [
            {
                "cameraId": "cam-1",
                "record": [
                    {"start": "2026-07-29T00:00:00Z", "end": "2026-07-29T01:00:00Z"},
                    {"start": "2026-07-29T01:00:00Z"},  # 缺 end
                    {"end": "2026-07-29T02:00:00Z"},     # 缺 start
                    {},                                  # 全缺
                ],
            },
        ]
    }
    result = parse_timeline_response(data)
    assert len(result["cam-1"]) == 1  # 只有第一筆有效


def test_parse_timeline_response_skips_malformed_camera_ids():
    """沒 cameraId 的 timeline 應跳過。"""
    data = {
        "timelines": [
            {"record": [{"start": "2026-07-29T00:00:00Z", "end": "2026-07-29T01:00:00Z"}]},
            {"cameraId": "cam-1", "record": []},
        ]
    }
    result = parse_timeline_response(data)
    assert "cam-1" in result
    assert len(result) == 1


# === compute_completeness ===

def test_compute_completeness_full_coverage_returns_1():
    """完全涵蓋 24h 視窗 → 完整率 1.0。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [(start, end)]
    assert compute_completeness(records, start, end) == 1.0


def test_compute_completeness_empty_returns_0():
    """完全沒錄 → 完整率 0.0。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    assert compute_completeness([], start, end) == 0.0


def test_compute_completeness_half_returns_0_5():
    """錄 12h → 完整率 0.5。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [(start, start + timedelta(hours=12))]
    assert compute_completeness(records, start, end) == 0.5


def test_compute_completeness_partial_overlap_clips_to_window():
    """record 超出視窗的部分不計；只算在視窗內的秒數。"""
    window_start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=24)
    # record 從視窗前 1 小時開始，到視窗內 11 小時
    records = [(window_start - timedelta(hours=1), window_start + timedelta(hours=11))]
    # 完全在視窗內的秒數 = 11h, 視窗 24h → 11/24
    assert compute_completeness(records, window_start, window_end) == pytest.approx(11 / 24)


def test_compute_completeness_clips_record_extending_past_window():
    """record 從視窗內開始，跨到視窗外 → 只算視窗內部分。"""
    window_start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=24)
    records = [(window_start + timedelta(hours=20), window_end + timedelta(hours=10))]
    # 視窗內只有 4h（20h 到 24h）
    assert compute_completeness(records, window_start, window_end) == pytest.approx(4 / 24)


def test_compute_completeness_merges_overlapping_records():
    """重疊的 record 視為一段（不重複計算）。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [
        (start, start + timedelta(hours=12)),
        (start + timedelta(hours=11), start + timedelta(hours=24)),  # 重疊 1h
    ]
    # 不重複計算 → 仍 1.0
    assert compute_completeness(records, start, end) == 1.0


def test_compute_completeness_zero_window_returns_zero():
    """window 為 0 秒（不合法）→ 完整率 0（避免除以 0）。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    assert compute_completeness([], start, start) == 0.0


# === compute_missing_segments ===

def test_compute_missing_segments_full_coverage_returns_empty():
    """完全沒缺口 → 空 list。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [(start, end)]
    assert compute_missing_segments(records, start, end) == []


def test_compute_missing_segments_empty_records_returns_full_window():
    """完全沒錄 → 整段視窗都是缺口。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    gaps = compute_missing_segments([], start, end)
    assert gaps == [(start, end)]


def test_compute_missing_segments_single_gap():
    """中段缺 1h → 一段缺口。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [
        (start, start + timedelta(hours=10)),
        (start + timedelta(hours=11), end),
    ]
    gaps = compute_missing_segments(records, start, end)
    assert len(gaps) == 1
    assert gaps[0] == (start + timedelta(hours=10), start + timedelta(hours=11))


def test_compute_missing_segments_leading_gap():
    """開頭缺 1h → 開頭缺口。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [(start + timedelta(hours=1), end)]
    gaps = compute_missing_segments(records, start, end)
    assert gaps == [(start, start + timedelta(hours=1))]


def test_compute_missing_segments_trailing_gap():
    """結尾缺 1h → 結尾缺口。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [(start, end - timedelta(hours=1))]
    gaps = compute_missing_segments(records, start, end)
    assert gaps == [(end - timedelta(hours=1), end)]


def test_compute_missing_segments_merges_overlapping_records_for_gap_calc():
    """重疊 record 應先合併才計算 gap，否則會誤判。"""
    start = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    records = [
        (start, start + timedelta(hours=12)),
        (start + timedelta(hours=11), end),  # 重疊 1h
    ]
    # 合併後 = 整段視窗 → 沒 gap
    gaps = compute_missing_segments(records, start, end)
    assert gaps == []
