"""
tests/test_camera_snapshots_db.py
==================================
SqliteWriter.camera_snapshots 表 + upsert_snapshot + get_snapshot_for_camera。

對齊 docs/superpowers/specs/2026-07-29-wall-thumbnail-redesign-design.md §1 架構。
"""
from __future__ import annotations

import gc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter


# === 共用素材 ===
def _make_jpeg(seed: int) -> bytes:
    """小 JPEG bytes（每個 seed 不同避免互相覆蓋時搞混）。"""
    from PIL import Image
    import io
    im = Image.new("RGB", (640, 480), (seed * 10 % 256, seed * 20 % 256, seed * 30 % 256))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def db_env():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def seeded_env(db_env):
    """灌一台 NVR + 一台 cam，回 (db_path, nvr_id, camera_id)。

    upsert_cameras / upsert_snapshot 都需要 begin_scan_run 先建立。
    """
    w = SqliteWriter(db_env)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "c1": {"name": "cam1", "connection_state": "CONNECTED"},
        "c2": {"name": "cam2", "connection_state": "CONNECTED"},
    })
    w._get_conn().commit()
    w.close()
    del w
    gc.collect()
    return db_env, nvra, "c1"


# === 1. schema 自動建立 ===
def test_camera_snapshots_table_created_on_init(db_env):
    """首次 SqliteWriter() 應自動建 camera_snapshots 表。"""
    SqliteWriter(db_env).close()
    conn = sqlite3.connect(db_env)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='camera_snapshots'"
        ).fetchone()
        assert row is not None, "camera_snapshots 表應自動建立"
        # 欄位檢查
        cols = [r[1] for r in conn.execute("PRAGMA table_info(camera_snapshots)").fetchall()]
        for required in ("id", "nvr_id", "camera_id", "jpeg_bytes", "width", "height", "captured_at"):
            assert required in cols, f"缺少欄位 {required}"
    finally:
        conn.close()


# === 2. upsert_snapshot 基本寫入 ===
def test_upsert_snapshot_writes_row(seeded_env):
    """首次 upsert_snapshot → 寫入 1 row。"""
    db_path, nvra, cam_id = seeded_env
    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_snapshot(nvra, cam_id, jpeg_bytes=_make_jpeg(1), width=160, height=120)
    w._get_conn().commit()
    w.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT nvr_id, camera_id, jpeg_bytes, width, height, captured_at "
            "FROM camera_snapshots"
        ).fetchall()
        assert len(rows) == 1
        nvr_id, cid, jpeg, w_, h_, captured_at = rows[0]
        assert nvr_id == nvra
        assert cid == cam_id
        assert jpeg == _make_jpeg(1)
        assert (w_, h_) == (160, 120)
        assert captured_at.endswith("Z")  # UTC ISO8601
    finally:
        conn.close()


# === 3. upsert_snapshot REPLACE ===
def test_upsert_snapshot_replaces_existing(seeded_env):
    """第二次 upsert_snapshot 同一 (nvr_id, camera_id) → 覆蓋、不新增。"""
    db_path, nvra, cam_id = seeded_env
    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_snapshot(nvra, cam_id, jpeg_bytes=_make_jpeg(1), width=160, height=120)
    w.upsert_snapshot(nvra, cam_id, jpeg_bytes=_make_jpeg(2), width=160, height=120)
    w._get_conn().commit()
    w.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT jpeg_bytes FROM camera_snapshots").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == _make_jpeg(2)  # 後者勝出
    finally:
        conn.close()


# === 4. 多 cam 各自獨立 ===
def test_upsert_snapshot_different_cameras(seeded_env):
    """不同 camera_id → 各存各的 row。"""
    db_path, nvra, _ = seeded_env
    w = SqliteWriter(db_path)
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_snapshot(nvra, "c1", jpeg_bytes=_make_jpeg(1), width=160, height=120)
    w.upsert_snapshot(nvra, "c2", jpeg_bytes=_make_jpeg(2), width=160, height=120)
    w._get_conn().commit()
    w.close()

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT camera_id FROM camera_snapshots ORDER BY camera_id"
        ).fetchall()
        assert [r[0] for r in rows] == ["c1", "c2"]
    finally:
        conn.close()


# === 5. 不同 NVR 隔離 ===
def test_upsert_snapshot_different_nvrs(db_env):
    """不同 nvr_id, 同一 camera_id → 各自存（UNIQUE 是 (nvr_id, camera_id)）。"""
    w = SqliteWriter(db_env)
    nvra = w.upsert_nvr({"id": "A", "name": "A", "host": "1.1.1.1", "port": 8443, "username": "u", "password": "p"})
    nvrb = w.upsert_nvr({"id": "B", "name": "B", "host": "2.2.2.2", "port": 8443, "username": "u", "password": "p"})
    w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {"c1": {"name": "cam1"}})
    w.upsert_cameras(nvrb, {"c1": {"name": "cam1"}})
    w.upsert_snapshot(nvra, "c1", jpeg_bytes=_make_jpeg(1), width=160, height=120)
    w.upsert_snapshot(nvrb, "c1", jpeg_bytes=_make_jpeg(2), width=160, height=120)
    w._get_conn().commit()
    w.close()

    conn = sqlite3.connect(db_env)
    try:
        rows = conn.execute(
            "SELECT nvr_id FROM camera_snapshots ORDER BY nvr_id"
        ).fetchall()
        assert [r[0] for r in rows] == [nvra, nvrb]
    finally:
        conn.close()