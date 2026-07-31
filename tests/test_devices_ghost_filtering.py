"""
tests/test_devices_ghost_filtering.py
=====================================
/devices 設備總覽頁應排除 ghost cam（is_ghost=1）。

根因：get_devices_paginated 沒過濾 is_ghost，
導致 NVR 已不再管理的 cam（rtsp:// 之類的幽靈）也列入總覽表。

設計：
- 修前 → /devices 顯示 3 支 cam（應只 2 支）
- 修後 → 只列 2 支真的，ghost 不見
"""
from __future__ import annotations

import gc
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def ghost_devices_app():
    """建 2 真 cam + 1 ghost cam，標記 ghost 為 is_ghost=1。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvr_int = w.upsert_nvr({
        "id": "NVR-A", "name": "real nvr", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-30T00:00:00Z")
    w.upsert_cameras(nvr_int, {
        "real-1": {"name": "cam1", "connection_state": "CONNECTED"},
        "real-2": {"name": "cam2", "connection_state": "CONNECTED"},
        "ghost-1": {"name": "rtsp://192.168.133.105:554/rtsp/x",
                     "connection_state": "DISCONNECTED"},
    })
    w.finish_scan_run(
        rid, finished_at="2026-07-30T00:01:00Z", status="complete",
        stats={"total_cameras": 3, "abnormal_cameras": 0,
               "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0},
    )
    w.close()

    # 模擬 mark_ghost_cameras 跑完
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE cameras SET is_ghost = 1 WHERE device_id = 'ghost-1'")
    conn.commit()
    conn.close()

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path

    del w, app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def ghost_client(ghost_devices_app):
    app, _ = ghost_devices_app
    return app.test_client()


# === 1. 總覽表排除 ghost cam ===
def test_devices_total_excludes_ghost_cams(ghost_client):
    """end-of-list 總筆數不包含 ghost（即 3 個 cams - 1 ghost = 2）。"""
    resp = ghost_client.get("/devices")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 修後：「共 2 筆」之類；修前：「共 3 筆」
    # 與 ghost-1 不該出現在 row
    assert "ghost-1" not in body, "/devices 仍包含 ghost cam"
    assert "rtsp://192.168.133.105" not in body, \
        "/devices 仍顯示 rtsp:// 幽靈 cam 名稱"
    # 真的 cam 應該在
    assert "cam1" in body
    assert "cam2" in body


# === 2. 真 cam 與 ghost 都在 DB，但只列 2 個 ===
def test_devices_returns_only_2_visible_rows(ghost_client):
    """驗證 HTML 內 /devices 表格行數 ≤ 3（table header + 2 row + pagination）。"""
    resp = ghost_client.get("/devices")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 取出所有 <tr> 包含 device_id 的行
    import re
    device_id_rows = re.findall(r"<code>(real-\d+|ghost-\d+)</code>", body)
    # 修前：3 個；修後：2 個
    assert len(device_id_rows) == 2, (
        f"預期 2 個 device_id rows (real-1 + real-2)，實際：{device_id_rows}"
    )
    assert "ghost-1" not in device_id_rows
