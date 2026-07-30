"""
tests/test_image_health_integration.py
=======================================
Phase 2.8（Arisan）Worker 整合測試：image_health stage 寫 DB + 觸發 events。

策略：mock AvigilonScanner 的 get_cameras() + fetch_thumbnail()，
直接呼叫 batch_scan._image_health_check_loop()，驗：
  1. image_health_checks row 寫入 + metrics_json / flags_json 正確
  2. 異常偵測 → 觸發 events 表（raw_json 含 source='image_health'）
  3. 凍結偵測：兩張同 jpeg → 觸發 frozen flag
  4. 異常偵測後 cameras.last_health_check_id 被更新
  5. 缺 connection_state CONNECTED 的 cam 跳過
  6. fetch_thumbnail 失敗 → 不中斷整批，記在 errors
"""
from __future__ import annotations

import gc
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from batch_scan import _image_health_check_loop
from db.sqlite_writer import SqliteWriter


# === Test fixtures ===
@pytest.fixture
def tmp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


def _make_jpeg(color: int, size: tuple[int, int] = (64, 64)) -> bytes:
    """製造指定灰度色的 JPEG bytes。"""
    img = Image.new("L", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_scanner_mock(cameras: dict, jpeg_bytes: bytes) -> MagicMock:
    """建立 mock AvigilonScanner，回傳固定 cameras + 固定 jpeg。"""
    scanner = MagicMock()
    scanner.get_cameras.return_value = cameras
    scanner.fetch_thumbnail.return_value = jpeg_bytes
    # 2026-07-30：batch_scan 改用 verbose 版；mock 也要對應
    scanner.fetch_thumbnail_with_status.return_value = (jpeg_bytes, None)
    return scanner


def _setup_writer_with_nvr(db_path: str, nvr_id: str = "NVR-T", cam_devices: list[str] | None = None) -> tuple[SqliteWriter, int, int]:
    """建 DB + upsert NVR + 灌 cams + 開 scan_run；回傳 (writer, nvr_int_id, run_id)。"""
    if cam_devices is None:
        cam_devices = []
    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr({
        "id": nvr_id, "name": nvr_id, "host": "1.1.1.1",
        "username": "u", "password": "p",
    })
    run_id = w.begin_scan_run("2026-07-17T00:00:00Z")
    cams_dict = {d: {"name": f"cam-{d}", "connection_state": "CONNECTED", "available": True} for d in cam_devices}
    w.upsert_cameras(nvr_int, cams_dict)
    return w, nvr_int, run_id


# === 1. 正常路徑：CONNECTED cam + 兩張相同 jpeg → 寫入 image_health_checks ===
def test_normal_path_writes_image_health_check(tmp_db_path):
    """正常流程：mock 1 台 cam 兩張相同 jpeg → 1 row 寫入。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(
        tmp_db_path, cam_devices=["d1"],
    )
    scanner = _make_scanner_mock(
        cameras={"d1": {"name": "cam1", "connection_state": "CONNECTED"}},
        jpeg_bytes=_make_jpeg(128),  # 中灰
    )
    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    assert summary["checked"] == 1
    # 中灰 + 凍結（兩張相同）→ 觸發 frozen + 中灰（中灰 mean_luma=0.502 不過曝/不欠曝/不模糊）
    # 所以觸發 1 個 frozen
    assert summary["triggered"] == 1

    # DB 驗證
    conn = w._require_active()
    rows = conn.execute(
        "SELECT camera_id, metrics_json, flags_json, triggered_event_ids "
        "FROM image_health_checks"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["camera_id"] == "d1"
    metrics = json.loads(row["metrics_json"])
    assert "blur_var" in metrics
    assert "mean_luma" in metrics
    assert "frozen_diff" in metrics
    assert metrics["is_frozen"] is True
    flags = json.loads(row["flags_json"])
    assert "frozen" in flags


# === 2. 異常 cam 跳過 ===
def test_non_connected_cam_is_skipped(tmp_db_path):
    """connection_state != CONNECTED 的 cam 跳過（不抓 jpeg）。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=[])
    scanner = _make_scanner_mock(
        cameras={
            "d1": {"name": "cam1", "connection_state": "CONNECTED"},
            "d2": {"name": "cam2", "connection_state": "DISCONNECTED"},
            "d3": {"name": "cam3", "connection_state": "LONG_FAILED"},
        },
        jpeg_bytes=_make_jpeg(128),
    )
    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)
    # 只 d1 跑過
    assert summary["checked"] == 1
    assert len(summary["errors"]) == 0


# === 3. 過曝偵測：全白 jpeg → 觸發 IMAGE_HEALTH_OVEREXPOSED event ===
def test_overexposed_triggers_event(tmp_db_path):
    """全白 jpeg → is_overexposed=True → 寫 events 表。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    # 用 function side_effect：第一次過曝（全白），第二次中灰（避免 frozen）
    call_count = [0]

    def fake_fetch(cam_id):
        call_count[0] += 1
        return _make_jpeg(255) if call_count[0] == 1 else _make_jpeg(128)

    scanner.fetch_thumbnail.side_effect = fake_fetch
    # 2026-07-30：batch_scan 改用 verbose 版；mock 也要對應
    scanner.fetch_thumbnail_with_status.side_effect = lambda cid: (fake_fetch(cid), None)
    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    assert summary["checked"] == 1
    assert summary["triggered"] >= 1

    conn = w._require_active()
    events = conn.execute(
        "SELECT event_topic, raw_json FROM events"
    ).fetchall()
    assert len(events) >= 1
    ev = events[0]
    assert ev["event_topic"].startswith("IMAGE_HEALTH_")
    raw = json.loads(ev["raw_json"])
    assert raw["source"] == "image_health"
    assert "flags" in raw
    assert "overexposed" in raw["flags"]


# === 4. cameras.last_health_check_id 更新 ===
def test_last_health_check_id_updated(tmp_db_path):
    """image_health 跑完後，cameras.last_health_check_id 應指向最新檢查。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = _make_scanner_mock(
        cameras={"d1": {"name": "cam1", "connection_state": "CONNECTED"}},
        jpeg_bytes=_make_jpeg(128),
    )
    _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    conn = w._require_active()
    last_id = conn.execute(
        "SELECT last_health_check_id FROM cameras WHERE device_id='d1'"
    ).fetchone()[0]
    assert last_id is not None
    assert last_id == 1  # 第一筆


# === 5. fetch_thumbnail 失敗 → 記 errors，不中斷 ===
def test_fetch_thumbnail_failure_does_not_crash(tmp_db_path):
    """fetch_thumbnail 回 None → 該 cam 跳過，整批繼續。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    scanner.fetch_thumbnail.return_value = None  # 永遠回 None
    # 2026-07-30：batch_scan 改用 verbose 版；mock 也要對應
    scanner.fetch_thumbnail_with_status.return_value = (None, "no jpeg (test)")
    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)
    assert summary["checked"] == 0  # 完全沒成功
    assert len(summary["errors"]) >= 1  # d1 失敗記錄


# === 6. triggered_event_ids 回填 ===
def test_triggered_event_ids_backfilled(tmp_db_path):
    """觸發 event 後，image_health_checks.triggered_event_ids 應填上 event_id。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    call_count = [0]

    def fake_fetch(cam_id):
        call_count[0] += 1
        return _make_jpeg(255) if call_count[0] == 1 else _make_jpeg(128)

    scanner.fetch_thumbnail.side_effect = fake_fetch
    # 2026-07-30：batch_scan 改用 verbose 版；mock 也要對應
    scanner.fetch_thumbnail_with_status.side_effect = lambda cid: (fake_fetch(cid), None)
    _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    conn = w._require_active()
    triggered_ids_json = conn.execute(
        "SELECT triggered_event_ids FROM image_health_checks"
    ).fetchone()[0]
    triggered_ids = json.loads(triggered_ids_json)
    assert len(triggered_ids) == 1
    # 該 event_id 應存在於 events 表
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_id=?", (triggered_ids[0],)
    ).fetchone()[0]
    assert ev_count == 1


# === 7. 全正常影像（兩張不同的高頻內容）→ 不觸發 event ===
def test_clean_image_no_event(tmp_db_path):
    """兩張內容豐富、差異大、亮度中等的影像 → 不觸發任何 flag。

    設計：pixel-level alternating（高頻 → gradient 大 → 不模糊；中灰 → 不過曝/欠曝）
          兩張差異大 → mean_abs_diff > 5 → 不凍結
    """
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    img_a = Image.new("L", (64, 64))
    for y in range(64):
        for x in range(64):
            img_a.putpixel((x, y), 200 if (x + y) % 2 == 0 else 50)
    img_b = Image.new("L", (64, 64))
    for y in range(64):
        for x in range(64):
            img_b.putpixel((x, y), 50 if (x + y) % 2 == 0 else 200)  # 反相
    call_count = [0]

    def fake_fetch(cam_id):
        call_count[0] += 1
        return _make_jpeg_with_pattern(img_a) if call_count[0] == 1 else _make_jpeg_with_pattern(img_b)

    scanner.fetch_thumbnail.side_effect = fake_fetch
    # 2026-07-30：batch_scan 改用 verbose 版；mock 也要對應
    scanner.fetch_thumbnail_with_status.side_effect = lambda cid: (fake_fetch(cid), None)
    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)
    assert summary["checked"] == 1
    assert summary["triggered"] == 0  # 沒觸發 → 不寫 events

    conn = w._require_active()
    ev_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert ev_count == 0


def _make_jpeg_with_pattern(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# === 8. get_cameras 失敗 → 整批早退，不 crash ===
def test_get_cameras_failure_returns_empty_summary(tmp_db_path):
    """scanner.get_cameras() 拋例外 → summary 內 errors 記錄，無 row 寫入。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path)
    scanner = MagicMock()
    scanner.get_cameras.side_effect = RuntimeError("NVR 不給看")

    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)
    assert summary["checked"] == 0
    assert any("get_cameras" in e for e in summary["errors"])

    conn = w._require_active()
    assert conn.execute("SELECT COUNT(*) FROM image_health_checks").fetchone()[0] == 0