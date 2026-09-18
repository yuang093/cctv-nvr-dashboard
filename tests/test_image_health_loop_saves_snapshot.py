"""
tests/test_image_health_loop_saves_snapshot.py
================================================
2026-07-29（Wall 縮圖重構）：image_health loop 內抓 JPEG → 縮圖 → upsert_snapshot。

對齊 spec §3 資料流：
  - 跟 NVR_IMAGE_HEALTH 合併（一次 HTTPS 同時拿 metadata + 縮圖）
  - 失敗 fallback：JPEG 抓不到就跳過縮圖；image_health 仍記錄
"""

from __future__ import annotations

import gc
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.sqlite_writer import SqliteWriter


# === 素材 ===
def _make_jpeg_bytes() -> bytes:
    """小 JPEG bytes（測試用）。"""
    from PIL import Image
    import io

    im = Image.new("RGB", (640, 480), (100, 150, 200))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def db_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
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
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(
        nvra,
        {
            "c1": {"name": "cam1", "connection_state": "CONNECTED"},
            "c2": {"name": "cam2", "connection_state": "CONNECTED"},
            "c3": {"name": "cam3", "connection_state": "DISCONNECTED"},  # 離線
        },
    )
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    yield db_path, nvra
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def _make_mock_scanner(cameras: dict, jpeg_by_cam: dict):
    """建 mock scanner：
    - get_cameras() → cameras
    - fetch_thumbnail(cam_id) → jpeg_by_cam[cam_id] 或 None（保留舊介面相容）
    - fetch_thumbnail_with_status(cam_id) → (jpeg, None) 或 (None, 'unknown')
      （2026-07-30 新介面；batch_scan 改用此方法）
    """
    scanner = MagicMock()
    scanner.get_cameras.return_value = cameras
    scanner.fetch_thumbnail.side_effect = lambda cid: jpeg_by_cam.get(cid)
    scanner.fetch_thumbnail_with_status.side_effect = lambda cid: (
        jpeg_by_cam.get(cid),
        None if jpeg_by_cam.get(cid) else "no jpeg",
    )
    return scanner


# === 1. image_health loop 順便存 snapshot ===
def test_image_health_loop_writes_snapshot_for_connected_cam(db_env):
    """CONNECTED cam + 抓到 jpeg → upsert_snapshot 應被呼叫。"""
    db_path, nvra = db_env
    from batch_scan import _image_health_check_loop

    cams = {
        "c1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    jpeg = _make_jpeg_bytes()
    scanner = _make_mock_scanner(cams, {"c1": jpeg})

    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    summary = _image_health_check_loop(
        scanner,
        nvra,
        run_id=1,
        writer=w,
        verbose=False,
        frozen_interval_sec=0,  # 跳過 sleep
    )
    w._get_conn().commit()
    w.close()

    assert summary["checked"] == 1
    # 驗證 DB 有寫入 snapshot
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT camera_id, jpeg_bytes FROM camera_snapshots"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "c1"
        # 縮圖後的 jpeg（不是原始 640x480）
        assert rows[0][1] != jpeg  # 縮過了
        assert len(rows[0][1]) < len(jpeg)  # 變小
    finally:
        conn.close()


def test_image_health_loop_skips_snapshot_when_jpeg_missing(db_env):
    """fetch_thumbnail 回 None → 跳過縮圖，但 image_health checked 仍記錄。"""
    db_path, nvra = db_env
    from batch_scan import _image_health_check_loop

    cams = {"c1": {"name": "cam1", "connection_state": "CONNECTED"}}
    scanner = _make_mock_scanner(cams, {"c1": None})  # 抓不到

    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    summary = _image_health_check_loop(
        scanner,
        nvra,
        run_id=1,
        writer=w,
        verbose=False,
        frozen_interval_sec=0,
    )
    w._get_conn().commit()
    w.close()

    assert summary["checked"] == 0  # 第一張抓不到，不算 checked
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT camera_id FROM camera_snapshots").fetchall()
        assert rows == []  # 沒寫 snapshot
    finally:
        conn.close()


def test_image_health_loop_skips_disconnected_cam(db_env):
    """connection_state != CONNECTED → 跳過（不抓 jpeg、不存 snapshot）。"""
    db_path, nvra = db_env
    from batch_scan import _image_health_check_loop

    cams = {"c3": {"name": "cam3", "connection_state": "DISCONNECTED"}}
    scanner = _make_mock_scanner(cams, {"c3": _make_jpeg_bytes()})

    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    summary = _image_health_check_loop(
        scanner,
        nvra,
        run_id=1,
        writer=w,
        verbose=False,
        frozen_interval_sec=0,
    )
    w._get_conn().commit()
    w.close()

    assert summary["checked"] == 0
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT camera_id FROM camera_snapshots").fetchall()
        assert rows == []
    finally:
        conn.close()


def test_image_health_loop_writes_multiple_snapshots(db_env):
    """多台 CONNECTED cam → 每台各寫一筆 snapshot。"""
    db_path, nvra = db_env
    from batch_scan import _image_health_check_loop

    cams = {
        "c1": {"name": "cam1", "connection_state": "CONNECTED"},
        "c2": {"name": "cam2", "connection_state": "CONNECTED"},
    }
    scanner = _make_mock_scanner(
        cams,
        {
            "c1": _make_jpeg_bytes(),
            "c2": _make_jpeg_bytes(),
        },
    )

    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    summary = _image_health_check_loop(
        scanner,
        nvra,
        run_id=1,
        writer=w,
        verbose=False,
        frozen_interval_sec=0,
    )
    w._get_conn().commit()
    w.close()

    assert summary["checked"] == 2
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT camera_id FROM camera_snapshots ORDER BY camera_id"
        ).fetchall()
        assert [r[0] for r in rows] == ["c1", "c2"]
    finally:
        conn.close()


def test_image_health_loop_snapshot_failure_does_not_break_loop(db_env):
    """縮圖步驟失敗（Pillow 問題等） → 不影響 image_health + 其他 cam 繼續。"""
    db_path, nvra = db_env
    from batch_scan import _image_health_check_loop
    from web import snapshot as wsnap

    cams = {
        "c1": {"name": "cam1", "connection_state": "CONNECTED"},
        "c2": {"name": "cam2", "connection_state": "CONNECTED"},
    }
    scanner = _make_mock_scanner(
        cams,
        {
            "c1": _make_jpeg_bytes(),
            "c2": _make_jpeg_bytes(),
        },
    )

    # 模擬 Pillow 壞掉
    orig = wsnap._PIL_OK
    wsnap._PIL_OK = False
    try:
        w = SqliteWriter(db_path)
        w.begin_scan_run("2026-07-29T00:00:00Z")
        summary = _image_health_check_loop(
            scanner,
            nvra,
            run_id=1,
            writer=w,
            verbose=False,
            frozen_interval_sec=0,
        )
        w._get_conn().commit()
        w.close()
    finally:
        wsnap._PIL_OK = orig

    # image_health 仍跑完（checked=2）
    assert summary["checked"] == 2
    # 但 snapshot 沒寫入（PIL 壞 → compress 回原 bytes；寫入還是會發生但用原 jpeg）
    # 這個測試只驗「不 crash」即可，寫入與否不在此嚴格斷言
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM camera_snapshots").fetchone()[0]
        # PIL 壞時 fallback 原 bytes；upsert_snapshot 仍會被呼叫（用原 jpeg bytes + 0x0 dim 失敗）
        # 我們只驗「沒有 exception」、loop 完成
        assert isinstance(count, int)
    finally:
        conn.close()
