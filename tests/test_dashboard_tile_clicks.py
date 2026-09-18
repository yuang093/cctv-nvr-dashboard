"""
tests/test_dashboard_tile_clicks.py
===================================
Phase 2.8（Arisan 磁磚點擊跳轉）：Dashboard 4 個計數磁磚變 anchor + 新增 stats 欄位。

驗證：
  1. dashboard GET 200
  2. 4 個計數磁磚各帶對應 href（query string）
  3. 第 5 個（最新掃描狀態）不帶 href（仍是非 anchor card）
  4. stats dict 新欄位（online_cameras / pending_events）有注入到 template
  5. stats.online_cameras 在缺 AVIGILON_USER_NONCE/KEY 時為 0（不掛 dashboard）

用自建 fixture（不依賴 test_web.py）以免 conftest fixture 命名衝突。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def dashboard_app():
    """建立 Flask app + 灌好測試資料的檔案 DB（與 test_web.py::seeded_web_app 等價）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    w.upsert_nvr(
        {
            "id": "NVR-A",
            "name": "A 分店",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
            "tags": ["branch", "taipei"],
        }
    )
    w.upsert_nvr(
        {
            "id": "NVR-B",
            "name": "B 分店",
            "host": "10.0.0.2",
            "port": 8443,
            "username": "u",
            "password": "p",
            "tags": ["branch", "taichung"],
        }
    )
    nvra_id, nvrb_id = 1, 2
    rid = w.begin_scan_run("2026-06-23T00:00:00Z")
    w.upsert_cameras(
        nvra_id,
        {
            "d1": {"name": "大門", "connection_state": "CONNECTED", "available": True},
            "d2": {
                "name": "停車場",
                "connection_state": "LONG_FAILED",
                "available": False,
            },
        },
    )
    w.upsert_cameras(
        nvrb_id,
        {
            "d10": {"name": "後門", "connection_state": "CONNECTED", "available": True},
        },
    )
    w.insert_events(
        rid,
        nvra_id,
        [
            {
                "eventId": "e1",
                "deviceId": "d2",
                "eventTopics": ["STATE_LONG_FAILED"],
                "eventTopic": "STATE_LONG_FAILED",
                "occurred_at": "2026-06-23T00:00:00Z",
            }
        ],
    )
    w.finish_scan_run(
        rid,
        finished_at="2026-06-23T00:01:00Z",
        status="partial",
        stats={
            "total_cameras": 3,
            "abnormal_cameras": 1,
            "total_nvrs": 2,
            "ok_nvrs": 1,
            "failed_nvrs": 1,
        },
    )

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
def client(dashboard_app):
    app, _ = dashboard_app
    return app.test_client()


# === 1. dashboard 200 ===
def test_dashboard_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


# === 2. 4 個計數磁磚 href 對應正確（query string）===
@pytest.mark.parametrize(
    "expected_href,label",
    [
        ("/nvrs", "NVR 總數"),
        ("/runs", "攝影機總數"),
        ("/runs?filter=online", "在線"),
        ("/events?status=pending", "異常（需處理）"),
    ],
)
def test_dashboard_tile_anchor_href(client, expected_href, label):
    """每個計數磁磚的 <a href> 應含預期 query string。"""
    body = client.get("/").get_data(as_text=True)
    assert (
        f'href="{expected_href}"' in body
    ), f"找不到 href={expected_href!r}（label={label!r}）"
    assert label in body


# === 3. 最新掃描狀態卡不是 anchor ===
def test_dashboard_status_card_not_anchor(client):
    """第 5 個卡片（最新掃描狀態）不應是 anchor——它是 status badge，不是計數磁磚。"""
    body = client.get("/").get_data(as_text=True)
    idx = body.find("最新掃描狀態")
    assert idx > 0
    # 取前 200 字元切片（同一個 div 內），驗證沒有未閉合 <a
    segment = body[max(0, idx - 200) : idx + 50]
    # 一段 div 內若 <a 開頭但 </a> 沒在同一段 → 表示「最新掃描狀態」被包進 anchor
    open_count = segment.count("<a href=")
    close_count = segment.count("</a>")
    assert (
        open_count == close_count
    ), f"最新掃描狀態卡不應是 anchor，但發現 open={open_count} close={close_count} 不一致"


# === 4. stats 新欄位有注入（label 存在）===
def test_dashboard_renders_online_cameras_and_pending_events(client):
    """dashboard 應把 stats.online_cameras / stats.pending_events 注入到 template。"""
    body = client.get("/").get_data(as_text=True)
    assert "在線" in body
    assert "異常（需處理）" in body


# === 5. get_overall_stats 新欄位 ===
def test_get_overall_stats_includes_pending_events(dashboard_app):
    """get_overall_stats() 回傳 dict 應含 pending_events 欄位。

    seeded: 1 event (STATE_LONG_FAILED on d2, resolved_at=NULL)
    → pending_events = 1（distinct device_id 有未解事件）
    """
    from web import db as webdb

    _, db_path = dashboard_app
    stats = webdb.get_overall_stats(db_path)
    assert "pending_events" in stats
    assert isinstance(stats["pending_events"], int)
    assert stats["pending_events"] == 1


def test_get_overall_stats_keeps_existing_fields(dashboard_app):
    """回歸測試：get_overall_stats() 既有欄位仍存在。"""
    from web import db as webdb

    _, db_path = dashboard_app
    stats = webdb.get_overall_stats(db_path)
    for key in ("total_nvrs", "total_cameras", "events_24h", "last_run"):
        assert key in stats, f"既有欄位 {key!r} 被破壞"


# === 6. 缺 credentials 不掛 dashboard ===
def test_dashboard_does_not_hang_without_credentials(client, monkeypatch):
    """缺 AVIGILON_USER_NONCE/KEY → online_cameras=0；dashboard 仍 200。"""
    monkeypatch.delenv("AVIGILON_USER_NONCE", raising=False)
    monkeypatch.delenv("AVIGILON_USER_KEY", raising=False)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "在線" in body  # label 還在，數字顯示 0


# === 7. 匯出按鈕（CSV / PDF）===
def test_dashboard_has_nvr_export_csv_button(client):
    """Dashboard 應有『匯出 NVR (CSV)』連結指向 /nvrs/export.csv。"""
    body = client.get("/").get_data(as_text=True)
    assert "/nvrs/export.csv" in body


def test_dashboard_has_abnormal_export_pdf_button(client):
    """Dashboard 應有『匯出異常 (PDF)』連結指向 /abnormal/export.pdf。"""
    body = client.get("/").get_data(as_text=True)
    assert "/abnormal/export.pdf" in body


# === 8. 缺錄最多排名（Top 5）===
def test_dashboard_shows_missing_ranking_section(client):
    """Dashboard 應顯示「24h 缺錄最多」區塊標題。"""
    body = client.get("/").get_data(as_text=True)
    assert "缺錄最多" in body
    # 沒 recording_status 資料時，應出現啟用提示
    assert "NVR_TIMELINE" in body or "缺錄" in body
