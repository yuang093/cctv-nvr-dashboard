"""
tests/test_wall_with_snapshots.py
=================================
web.db.get_wall_cameras_with_snapshots()：8444 /wall 用的視覺化資料（含縮圖 base64）。

對齊 docs/superpowers/specs/2026-07-29-wall-thumbnail-redesign-design.md §3：
  - LEFT JOIN camera_snapshots 取最新縮圖
  - category = signal_lost | no_signal | online（沿用既有分類邏輯）
  - snapshot_b64 為 base64 string；沒快照 → None
  - 預設排序：signal_lost > no_signal > online（同類反序）
"""
from __future__ import annotations

import base64
import gc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_wall_cameras_with_snapshots


# === 素材 ===
def _make_jpeg(seed: int) -> bytes:
    from PIL import Image
    import io
    im = Image.new("RGB", (160, 120), (seed * 10 % 256, seed * 20 % 256, seed * 30 % 256))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def seeded_env():
    """1 NVR + 3 cam (c1 online / c2 signal_lost / c3 no_signal)。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "c1": {"name": "cam1", "connection_state": "CONNECTED", "ip_address": "192.168.1.1"},
        "c2": {"name": "cam2", "connection_state": "CONNECTED", "ip_address": "192.168.1.2"},
        "c3": {"name": "cam3", "connection_state": "LONG_FAILED", "ip_address": "192.168.1.3"},
    })
    # c2: 未解 DEVICE_VIDEO_SIGNAL_LOST
    w.insert_events(w._current_scan_run_id, nvra, [{
        "eventId": "e2", "deviceId": "c2",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-29T01:00:00Z",
    }])
    # c3: 未解 STATE_LONG_FAILED（DEVICE_LONG_FAILED 在 _NO_SIGNAL_TOPICS）
    w.insert_events(w._current_scan_run_id, nvra, [{
        "eventId": "e3", "deviceId": "c3",
        "eventTopics": ["DEVICE_LONG_FAILED"],
        "eventTopic": "DEVICE_LONG_FAILED",
        "occurred_at": "2026-07-29T02:00:00Z",
    }])
    # c2 有快照、c3 沒快照
    w.upsert_snapshot(nvra, "c2", jpeg_bytes=_make_jpeg(1), width=160, height=120)
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    yield db_path
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 1. 基本回傳 ===
def test_returns_list_of_cameras(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    assert isinstance(rows, list)
    assert len(rows) == 3


def test_each_row_has_required_keys(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    required = {"device_id", "camera_name", "nvr_name", "category", "snapshot_b64", "has_snapshot"}
    for r in rows:
        assert required.issubset(r.keys()), f"缺欄位：{required - r.keys()}"


def test_snapshot_b64_for_camera_with_snapshot(seeded_env):
    """c2 有快照 → snapshot_b64 應是該 JPEG 的 base64。"""
    rows = get_wall_cameras_with_snapshots(seeded_env)
    by_id = {r["device_id"]: r for r in rows}
    c2 = by_id["c2"]
    assert c2["has_snapshot"] is True
    assert isinstance(c2["snapshot_b64"], str)
    decoded = base64.b64decode(c2["snapshot_b64"])
    assert decoded[:2] == b"\xff\xd8"  # JPEG magic
    # 跟原始 _make_jpeg(1) 一樣
    assert decoded == _make_jpeg(1)


def test_snapshot_none_for_camera_without_snapshot(seeded_env):
    """c3 沒快照 → snapshot_b64=None, has_snapshot=False。"""
    rows = get_wall_cameras_with_snapshots(seeded_env)
    by_id = {r["device_id"]: r for r in rows}
    c3 = by_id["c3"]
    assert c3["has_snapshot"] is False
    assert c3["snapshot_b64"] is None


# === 2. 分類正確 ===
def test_category_signal_lost(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    by_id = {r["device_id"]: r for r in rows}
    assert by_id["c2"]["category"] == "signal_lost"


def test_category_no_signal(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    by_id = {r["device_id"]: r for r in rows}
    assert by_id["c3"]["category"] == "no_signal"


def test_category_online(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env)
    by_id = {r["device_id"]: r for r in rows}
    assert by_id["c1"]["category"] == "online"


# === 3. 篩選 ===
def test_filter_online_returns_only_online(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env, filter_kind="online")
    assert all(r["category"] == "online" for r in rows)
    assert {r["device_id"] for r in rows} == {"c1"}


def test_filter_signal_lost_returns_only_signal_lost(seeded_env):
    rows = get_wall_cameras_with_snapshots(seeded_env, filter_kind="signal_lost")
    assert all(r["category"] == "signal_lost" for r in rows)
    assert {r["device_id"] for r in rows} == {"c2"}


def test_filter_invalid_falls_back_to_all(seeded_env):
    """未知 filter_kind → 當作 all。"""
    rows = get_wall_cameras_with_snapshots(seeded_env, filter_kind="bogus")
    assert len(rows) == 3


# === 4. 空 DB ===
def test_returns_empty_when_no_cameras():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    SqliteWriter(db_path).close()
    rows = get_wall_cameras_with_snapshots(db_path)
    assert rows == []
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 5. 排序（單獨寫在 test_wall_sort_by_severity.py；這裡只驗證有排序欄位） ===
def test_includes_latest_event_at_for_sorting(seeded_env):
    """rows 含 latest_event_at 欄位（給 SQL 排序用）。"""
    rows = get_wall_cameras_with_snapshots(seeded_env)
    for r in rows:
        assert "latest_event_at" in r


# === 6. nvr_id 篩選（Task 1：給 /fleet 取單一 NVR 的 cam 清單） ===
def test_get_wall_cameras_with_snapshots_filters_by_nvr_id():
    """傳 nvr_id 參數時只回該 NVR 的 cam。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({"id": "A", "name": "A店", "host": "1.1.1.1", "port": 8443, "username": "u", "password": "p"})
    nvrb = w.upsert_nvr({"id": "B", "name": "B店", "host": "2.2.2.2", "port": 8443, "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "d1": {"name": "A大門", "connection_state": "CONNECTED"},
        "d2": {"name": "A後門", "connection_state": "CONNECTED"},
    })
    w.upsert_cameras(nvrb, {
        "d10": {"name": "B大門", "connection_state": "CONNECTED"},
    })
    w.finish_scan_run(rid, finished_at="2026-07-29T00:01:00Z", status="success",
                      stats={"total_cameras": 3, "abnormal_cameras": 0,
                             "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0})
    del w
    gc.collect()

    only_a = get_wall_cameras_with_snapshots(db_path, nvr_id=nvra)
    assert len(only_a) == 2
    assert all(c["nvr_name"] == "A店" for c in only_a)

    only_b = get_wall_cameras_with_snapshots(db_path, nvr_id=nvrb)
    assert len(only_b) == 1
    assert only_b[0]["nvr_name"] == "B店"

    # 不傳 nvr_id → 回全部（向後相容）
    all_cams = get_wall_cameras_with_snapshots(db_path)
    assert len(all_cams) == 3

    Path(db_path).unlink(missing_ok=True)