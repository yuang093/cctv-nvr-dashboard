"""
tests/test_image_health_frozen_no_event.py
===========================================
2026-07-30：frozen 不再觸 events 表（修法 N）。

問題：cam B 「明亮低動態場景」(luma 0.41 + frozen_diff ~0.25) 持續 IMAGE_HEALTH_FROZEN
誤報 80% 命中率。實驗證明 pixel diff / Laplacian 都無法區分「真凍結」與「ISP 微調」。

修法：frozen 仍寫 metrics（image_health_checks 內 is_frozen + flags=frozen），
**但不寫 events 表**。其他 trigger（blur / overexposed / underexposed）仍正常觸發 event。

代價：真凍結不再觸發 IMAGE_HEALTH_FROZEN event（反正救不了）。
未來若要智慧化凍結偵測，重寫 is_frozen 演算法即可，metrics 一直在。
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
    """製造指定灰度色的 JPEG bytes（中灰 luma=0.50，確保不被 dark skip）。"""
    img = Image.new("L", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_jpeg_with_structure(low: int = 0, high: int = 255, size: tuple[int, int] = (64, 64)) -> bytes:
    """生成 alternating 結構的灰階 JPEG（有 edge，避免被當 blurry）。

    設計：(x+y)%2 決定亮度 → 高頻變化 → gradient variance 大 → not blurry。
    同時 luma ≈ (low+high)/2 / 255 ≈ 0.50 → not over/underexposed。
    兩張相同 → is_frozen=True。

    注意：必須用 0/255 alternating（gradient = 255 大於 threshold 100）。
    用小範圍（e.g. 100/156）gradient 太小，仍會被判 blurry。
    """
    img = Image.new("L", size)
    for y in range(size[1]):
        for x in range(size[0]):
            img.putpixel((x, y), low if (x + y) % 2 == 0 else high)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_scanner_mock(cameras: dict, jpeg_bytes: bytes) -> MagicMock:
    scanner = MagicMock()
    scanner.get_cameras.return_value = cameras
    scanner.fetch_thumbnail.return_value = jpeg_bytes
    scanner.fetch_thumbnail_with_status.return_value = (jpeg_bytes, None)
    return scanner


def _setup_writer_with_nvr(db_path: str, nvr_id: str = "NVR-T", cam_devices: list[str] | None = None) -> tuple[SqliteWriter, int, int]:
    if cam_devices is None:
        cam_devices = []
    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr({
        "id": nvr_id, "name": nvr_id, "host": "1.1.1.1",
        "username": "u", "password": "p",
    })
    run_id = w.begin_scan_run("2026-07-30T00:00:00Z")
    cams_dict = {d: {"name": f"cam-{d}", "connection_state": "CONNECTED", "available": True} for d in cam_devices}
    w.upsert_cameras(nvr_int, cams_dict)
    return w, nvr_int, run_id


# === 1. 核心修法：frozen 不觸 event ===
def test_frozen_no_longer_triggers_event(tmp_db_path):
    """兩張相同 JPEG（luma=0.50 中灰，避開 dark skip）
    → is_frozen=True → 不寫 events 表。

    仍寫：image_health_checks row（flags 含 frozen / is_frozen=True）、
          triggered_event_ids = []、
          cameras.last_health_check_id 更新。
    """
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = _make_scanner_mock(
        cameras={"d1": {"name": "cam1", "connection_state": "CONNECTED"}},
        jpeg_bytes=_make_jpeg_with_structure(),  # 0/255 alternating → frozen only
    )
    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    # summary: checked=1, triggered=0（frozen 不算 triggered）
    assert summary["checked"] == 1
    assert summary["triggered"] == 0, "frozen 不該計入 triggered"

    conn = w._require_active()
    # image_health_checks 仍寫入 1 row，flags 含 frozen
    rows = conn.execute(
        "SELECT camera_id, metrics_json, flags_json, triggered_event_ids "
        "FROM image_health_checks"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["camera_id"] == "d1"
    flags = json.loads(row["flags_json"])
    assert "frozen" in flags, "metrics 仍記 frozen flag"
    triggered = json.loads(row["triggered_event_ids"])
    assert triggered == [], "triggered_event_ids 應為空"

    # events 表不應有 IMAGE_HEALTH_FROZEN
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0]
    assert ev_count == 0, "frozen 不該寫 events"


# === 2. frozen + blurry 同時：仍寫 event（topic=BLURRY） ===
def test_frozen_plus_blurry_still_triggers_blur_event(tmp_db_path):
    """如果 cam 同時 frozen + blurry（罕見但可能）→ event 仍寫（topic=BLURRY）。

    寫法：blur 用 blur_var < threshold 的圖（高頻圖 JPEG 重壓模糊）。
    暫難 mock → 改用 overexposed 觸發（更易控）。
    """
    # 構造 overexposed JPEG（全白 luma=255 > 0.9）+ 跟前一張完全相同
    # → is_frozen=True + is_overexposed=True
    # 兩個 flag → 仍應觸 event（因為 "frozen" 之外還有 "overexposed"）
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])

    # 兩張都用 alternating 結構（全白會被當 blurry+overexposed；純灰會被當 blurry）
    # 直接 mock：既有圖 frozen=True + overexposed=True 的罕見組合難造。
    # 改測：用 alternating 結構 + jpeg_a / jpeg_b 第二張 trigger overexposed via 設計：
    # mock fetch_thumbnail.side_effect 第一次 alternating、第二次全白 → 不會 frozen
    # 但這又失去「frozen + other」的目的。
    # 簡化：這個測試直接驗證「多 flag」路徑，現有邏輯 + 新邏輯都能跑出 triggered=1。
    # 重新設計：fetch_thumbnail 第一次 alternating（frozen 信號），第二次全白（overexposed）
    # → jpeg_a alternating, jpeg_b overexposed → 不同 → NOT frozen
    # → flags 只有 overexposed → event topic = OVEREXPOSED ✓
    # 不對，這要 frozen+overexposed 同時才有意義。
    # 改方案：兩張都全白 → frozen=True + overexposed=True → event topic 仍 OVEREXPOSED（priority 高）
    # 全白 JPEG 仍會被當 blurry（純色 var=0），但 primary_topic 邏輯會先挑 OVEREXPOSED。
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    white = _make_jpeg(255)
    scanner.fetch_thumbnail.return_value = white
    scanner.fetch_thumbnail_with_status.return_value = (white, None)

    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    # triggered=1（overexposed 觸發）— frozen 不單獨計入
    assert summary["triggered"] == 1, "overexposed 應觸 event"
    conn = w._require_active()
    ev_rows = conn.execute("SELECT event_topic FROM events").fetchall()
    assert len(ev_rows) == 1
    # primary_topic 順序：BLURRY > OVEREXPOSED > UNDEREXPOSED > ANOMALY
    # 全白 JPEG 是 blurry（純色 var=0）+ overexposed（luma=1.0），primary=BLURRY
    # （這算法 priority 是設計權衡，不在本測試範圍；我們只驗 triggered=1 與 event 寫入）
    assert ev_rows[0][0] in ("IMAGE_HEALTH_BLURRY", "IMAGE_HEALTH_OVEREXPOSED")


# === 3. frozen + overexposed：回填 triggered_event_ids 正常 ===
def test_frozen_plus_overexposed_triggered_event_ids_backfilled(tmp_db_path):
    """frozen + overexposed → triggered_event_ids 內有 1 個 event_id。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1"])
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
    }
    white = _make_jpeg(255)
    scanner.fetch_thumbnail.return_value = white
    scanner.fetch_thumbnail_with_status.return_value = (white, None)

    _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    conn = w._require_active()
    triggered = json.loads(conn.execute(
        "SELECT triggered_event_ids FROM image_health_checks"
    ).fetchone()[0])
    assert len(triggered) == 1, "overexposed 觸發的 event_id 應回填"


# === 4. summary 統計：frozen 不算 triggered ===
def test_summary_triggered_count_excludes_frozen(tmp_db_path):
    """驗證 summary["triggered"] 只算會寫 event 的 flag，不含 frozen。"""
    w, nvr_int, run_id = _setup_writer_with_nvr(tmp_db_path, cam_devices=["d1", "d2"])
    scanner = MagicMock()
    scanner.get_cameras.return_value = {
        "d1": {"name": "cam1", "connection_state": "CONNECTED"},
        "d2": {"name": "cam2", "connection_state": "CONNECTED"},
    }
    structured = _make_jpeg_with_structure()  # 0/255 alternating → frozen only
    scanner.fetch_thumbnail.return_value = structured
    scanner.fetch_thumbnail_with_status.return_value = (structured, None)

    summary = _image_health_check_loop(scanner, nvr_int, run_id, w, verbose=False, frozen_interval_sec=0)

    # 兩台都 frozen → triggered = 0
    assert summary["checked"] == 2
    assert summary["triggered"] == 0
    assert summary["errors"] == []
