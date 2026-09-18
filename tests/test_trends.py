"""web/trends.py 純函式測試（無 Flask、無 HTTP）。"""

from __future__ import annotations

import json as _json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from web.trends import (
    compute_health_timeseries,
    get_all_cams_health_summary,
)


@pytest.fixture
def empty_db(tmp_path: Path) -> str:
    """建立空 SQLite + 必要 schema（cameras + nvr_servers + image_health_checks）。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            is_ghost INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE image_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            nvr_server_id INTEGER,
            checked_at_utc TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            flags_json TEXT NOT NULL
        );
        CREATE INDEX idx_health_cam_time
            ON image_health_checks(camera_id, checked_at_utc DESC);
    """)
    conn.commit()
    conn.close()
    return db_path


class TestComputeHealthTimeseries24h:
    def test_empty_db_returns_24_zero_bins(self, empty_db: str):
        """沒任何 record → 24 個 bin 全 0/0/0/0。"""
        bins = compute_health_timeseries(empty_db, "cam-unknown", range_hours=24)
        assert len(bins) == 24
        assert all(b.sample_count == 0 for b in bins)
        assert all(b.online_pct == 0.0 for b in bins)
        assert all(b.frozen_pct == 0.0 for b in bins)
        assert all(b.underexposed_pct == 0.0 for b in bins)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_offset(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _seed_record(
    db_path: str, cam_id: str, hours_ago: float, is_frozen: bool, is_underexposed: bool
) -> None:
    """塞一筆 image_health_checks record。"""
    metrics = {
        "blur_var": 100.0,
        "mean_luma": 0.5,
        "is_blurry": False,
        "is_overexposed": False,
        "is_underexposed": is_underexposed,
        "frozen_diff": 0.5,
        "is_frozen": is_frozen,
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO image_health_checks "
        "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
        "VALUES (?, NULL, ?, ?, '[]')",
        (cam_id, _iso_offset(hours_ago), _json.dumps(metrics)),
    )
    conn.commit()
    conn.close()


class TestComputeHealthTimeseries24hWithData:
    """24h + records：驗證 4 個核心場景（spec §7.1）。"""

    def test_all_healthy_24h(self, empty_db: str):
        """每 bin 各 2 筆 healthy record → online=100%, frozen=0%, underexposed=0%。"""
        # 24 bin * 2 = 48 筆，分布過去 24h
        for h in range(24):
            for _ in range(2):
                _seed_record(empty_db, "cam-1", h + 0.25, False, False)
        bins = compute_health_timeseries(empty_db, "cam-1", range_hours=24)
        assert len(bins) == 24
        # 至少有 record 的 bin 全 100% online；最舊幾個 bin 可能沒 record（fallback 0%）
        online_bins = [b for b in bins if b.sample_count > 0]
        assert len(online_bins) == 24, f"應有 24 bin 含 record，got {len(online_bins)}"
        assert all(b.online_pct == 100.0 for b in online_bins)
        assert all(b.frozen_pct == 0.0 for b in online_bins)
        assert all(b.underexposed_pct == 0.0 for b in online_bins)

    def test_frozen_spike_3_bins(self, empty_db: str):
        """第 5-7 bin 各有 1 筆 is_frozen → 那 3 bin frozen=100%。"""
        # seed 在 bin index 4, 5, 6 (0-indexed) ~ 對應 4-6h ago
        for h_ago in (4.2, 5.2, 6.2):
            _seed_record(empty_db, "cam-2", h_ago, True, False)
        bins = compute_health_timeseries(empty_db, "cam-2", range_hours=24)
        assert len(bins) == 24
        # 第 5, 6, 7 bin (索引 4, 5, 6) 應 frozen=100%
        for idx in (4, 5, 6):
            assert (
                bins[idx].frozen_pct == 100.0
            ), f"bin {idx} 應 frozen=100%, got {bins[idx].frozen_pct}"
            assert bins[idx].underexposed_pct == 0.0

    def test_underexposed_spike_3_bins(self, empty_db: str):
        """第 10-12 bin 各有 1 筆 is_underexposed → 那 3 bin underexposed=100%。"""
        for h_ago in (10.5, 11.5, 12.5):
            _seed_record(empty_db, "cam-3", h_ago, False, True)
        bins = compute_health_timeseries(empty_db, "cam-3", range_hours=24)
        for idx in (10, 11, 12):
            assert (
                bins[idx].underexposed_pct == 100.0
            ), f"bin {idx} 應 underexposed=100%, got {bins[idx].underexposed_pct}"
            assert bins[idx].frozen_pct == 0.0

    def test_offline_window_3_bins(self, empty_db: str):
        """第 10-12 bin 完全沒 record → online=0%（離線）。"""
        for h_ago in (5.0, 5.5, 6.0, 7.0, 7.5, 8.0):
            _seed_record(empty_db, "cam-4", h_ago, False, False)
        # bin 10-12 (15-18h ago) 沒 record
        bins = compute_health_timeseries(empty_db, "cam-4", range_hours=24)
        for idx in (10, 11, 12):
            assert (
                bins[idx].online_pct == 0.0
            ), f"bin {idx} 應離線 online=0%, got {bins[idx].online_pct}"
            assert bins[idx].sample_count == 0
        # bin 5-8 應有 record
        for idx in (5, 6, 7, 8):
            assert bins[idx].online_pct == 100.0
            assert bins[idx].sample_count >= 1

    def test_invalid_metrics_json_fallback(self, empty_db: str):
        """壞 JSON → 該筆 fallback，不 crash。"""
        conn = sqlite3.connect(empty_db)
        conn.execute(
            "INSERT INTO image_health_checks "
            "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
            "VALUES ('cam-bad', NULL, ?, 'not-json-at-all', '[]')",
            (_iso_offset(1.0),),
        )
        conn.commit()
        conn.close()
        # 不應丟例外
        bins = compute_health_timeseries(empty_db, "cam-bad", range_hours=24)
        assert len(bins) == 24
        # sample_count 應為 1（record 有讀到），但 frozen/underexposed=0
        nonzero = [b for b in bins if b.sample_count > 0]
        assert len(nonzero) >= 1
        assert all(b.frozen_pct == 0.0 for b in nonzero)
        assert all(b.underexposed_pct == 0.0 for b in nonzero)


class TestComputeHealthTimeseries7d:
    def test_7d_aggregates_to_daily_bins(self, empty_db: str):
        """range_hours=168 → 7 個 daily bins。"""
        for day in range(7):
            for hour in (0, 6, 12, 18):
                _seed_record(empty_db, "cam-5", day * 24 + (23 - hour), False, False)
        bins = compute_health_timeseries(empty_db, "cam-5", range_hours=168)
        assert len(bins) == 7, f"7d 應回 7 個 daily bin，got {len(bins)}"
        # 全部 bin 都應有 record（seed 分散 7 天）
        online_bins = [b for b in bins if b.sample_count > 0]
        assert len(online_bins) == 7, "7d 全部 7 天都有 record"
        assert all(b.online_pct == 100.0 for b in online_bins)

    def test_7d_invalid_range_raises(self, empty_db: str):
        """range_hours=99 → ValueError（不是 24 也不是 168）。"""
        with pytest.raises(ValueError, match="range_hours 必須"):
            compute_health_timeseries(empty_db, "cam-x", range_hours=99)


def _seed_nvr_and_cams(
    db_path: str, nvr_id: str, nvr_name: str, cam_specs: list[tuple[str, str, bool]]
) -> None:
    """塞 1 台 NVR + 數台 cam。cam_specs = [(device_id, name, is_ghost), ...]"""
    conn = sqlite3.connect(db_path)
    nvr_int = conn.execute(
        "INSERT INTO nvr_servers (nvr_id, name) VALUES (?, ?)",
        (nvr_id, nvr_name),
    ).lastrowid
    for device_id, name, is_ghost in cam_specs:
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nvr_int, device_id, name, 1 if is_ghost else 0, _now_iso()),
        )
    conn.commit()
    conn.close()


class TestGetAllCamsHealthSummary:
    """對應 spec §7.1: ghost / nvr filter / status filter / sort。"""

    def test_empty_db_returns_empty_list(self, empty_db: str):
        """空 DB → 空 list（不 crash）。"""
        result = get_all_cams_health_summary(empty_db, range_hours=24)
        assert result == []

    def test_filters_ghost_cams(self, empty_db: str):
        """is_ghost=1 的 cam 不在結果中。"""
        _seed_nvr_and_cams(
            empty_db,
            "nvr-a",
            "ACC-8",
            [
                ("d-1", "Cam1", False),
                ("d-2", "Ghost", True),  # 應過濾
                ("d-3", "Cam3", False),
            ],
        )
        result = get_all_cams_health_summary(empty_db, range_hours=24)
        ids = [s.cam_id for s in result]
        assert "d-2" not in ids, f"Ghost cam d-2 應過濾，got {ids}"
        assert set(ids) == {"d-1", "d-3"}

    def test_nvr_filter_only_returns_target_nvr(self, empty_db: str):
        """nvr_filter='nvr-a' 只列該 NVR 的 cam。"""
        _seed_nvr_and_cams(empty_db, "nvr-a", "ACC-8", [("d-1", "Cam1", False)])
        _seed_nvr_and_cams(empty_db, "nvr-b", "ACC-9", [("d-2", "Cam2", False)])
        result = get_all_cams_health_summary(
            empty_db, range_hours=24, nvr_filter="nvr-a"
        )
        ids = [s.cam_id for s in result]
        assert ids == ["d-1"], f"nvr_filter=nvr-a 應只剩 d-1，got {ids}"

    def test_abnormal_only_filter(self, empty_db: str):
        """status_filter='abnormal_only' 只列有 abnormal 的 cam。"""
        _seed_nvr_and_cams(
            empty_db,
            "nvr-a",
            "ACC-8",
            [
                ("healthy", "HealthyCam", False),
                ("frozen", "FrozenCam", False),
                ("underexposed", "DarkCam", False),
            ],
        )
        # healthy cam：8 筆 healthy record（分布 8h ago~15h ago）
        for h in range(8, 16):
            _seed_record(empty_db, "healthy", h, False, False)
        # frozen cam：3 bin frozen = 3 abnormal
        for h_ago in (4.2, 5.2, 6.2):
            _seed_record(empty_db, "frozen", h_ago, True, False)
        # underexposed cam：2 bin underexposed
        for h_ago in (10.5, 11.5):
            _seed_record(empty_db, "underexposed", h_ago, False, True)

        result = get_all_cams_health_summary(empty_db, range_hours=24)
        all_ids = {s.cam_id for s in result}
        assert all_ids == {"healthy", "frozen", "underexposed"}

        result_filtered = get_all_cams_health_summary(
            empty_db,
            range_hours=24,
            status_filter="abnormal_only",
        )
        filtered_ids = {s.cam_id for s in result_filtered}
        # healthy 0 abnormal → 過濾掉
        assert "healthy" not in filtered_ids
        assert "frozen" in filtered_ids
        assert "underexposed" in filtered_ids
        # 各 summary 的 abnormal_bins 數
        by_id = {s.cam_id: s.abnormal_bins for s in result_filtered}
        assert by_id["frozen"] == 3
        assert by_id["underexposed"] == 2

    def test_sorted_by_abnormal_bins_desc(self, empty_db: str):
        """排序：abnormal_bins DESC, cam_name ASC。"""
        _seed_nvr_and_cams(
            empty_db,
            "nvr-a",
            "ACC-8",
            [
                ("alpha", "Alpha", False),
                ("bravo", "Bravo", False),
                ("charlie", "Charlie", False),
            ],
        )
        # bravo 5 bins abnormal, alpha 2, charlie 0
        for h in range(2):
            for _ in range(2):
                _seed_record(empty_db, "bravo", h + 0.1, True, False)
                _seed_record(empty_db, "bravo", h + 0.2, True, False)
                _seed_record(empty_db, "bravo", h + 0.3, True, False)
        for h_ago in (1.5, 2.5):
            _seed_record(empty_db, "alpha", h_ago, True, False)

        result = get_all_cams_health_summary(empty_db, range_hours=24)
        ids = [s.cam_id for s in result]
        # bravo(5) > alpha(2) > charlie(0)
        assert ids.index("bravo") < ids.index("alpha") < ids.index("charlie")
