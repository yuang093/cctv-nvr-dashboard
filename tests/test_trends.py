"""web/trends.py 純函式測試（無 Flask、無 HTTP）。"""
from __future__ import annotations

import json as _json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from web.trends import (
    HealthBin,
    compute_health_timeseries,
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
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_record(db_path: str, cam_id: str, hours_ago: float,
                 is_frozen: bool, is_underexposed: bool) -> None:
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
        assert len(online_bins) >= 20, f"應有 ≥20 bin 含 record，got {len(online_bins)}"
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
            assert bins[idx].frozen_pct == 100.0, \
                f"bin {idx} 應 frozen=100%, got {bins[idx].frozen_pct}"
            assert bins[idx].underexposed_pct == 0.0

    def test_underexposed_spike_3_bins(self, empty_db: str):
        """第 10-12 bin 各有 1 筆 is_underexposed → 那 3 bin underexposed=100%。"""
        for h_ago in (10.5, 11.5, 12.5):
            _seed_record(empty_db, "cam-3", h_ago, False, True)
        bins = compute_health_timeseries(empty_db, "cam-3", range_hours=24)
        for idx in (10, 11, 12):
            assert bins[idx].underexposed_pct == 100.0, \
                f"bin {idx} 應 underexposed=100%, got {bins[idx].underexposed_pct}"
            assert bins[idx].frozen_pct == 0.0

    def test_offline_window_3_bins(self, empty_db: str):
        """第 10-12 bin 完全沒 record → online=0%（離線）。"""
        for h_ago in (5.0, 5.5, 6.0, 7.0, 7.5, 8.0):
            _seed_record(empty_db, "cam-4", h_ago, False, False)
        # bin 10-12 (15-18h ago) 沒 record
        bins = compute_health_timeseries(empty_db, "cam-4", range_hours=24)
        for idx in (10, 11, 12):
            assert bins[idx].online_pct == 0.0, \
                f"bin {idx} 應離線 online=0%, got {bins[idx].online_pct}"
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