"""web/trends.py 純函式測試（無 Flask、無 HTTP）。"""
from __future__ import annotations

import sqlite3
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