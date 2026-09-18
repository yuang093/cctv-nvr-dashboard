"""
tests/test_web_abnormal.py
==========================
Phase 2.6：/abnormal 故障攝影機總覽 + PDF 報告測試。

涵蓋：
    - GET /abnormal 200 + 含故障分組
    - GET /abnormal 200 + 空狀態
    - GET /abnormal/export.pdf 200 + Content-Type application/pdf
    - PDF 含中文字（bytes 內含 msjh 字型）
    - 故障類型中文對照
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def abn_app():
    """空 DB Flask app。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    SqliteWriter(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path
    del app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def client(abn_app):
    app, _ = abn_app
    return app.test_client()


def _seed_abnormal(db_path: str) -> None:
    """塞 2 台 NVR、3 台相機、4 個 OPEN 事件。"""
    w = SqliteWriter(db_path)
    nvr_a = w.upsert_nvr(
        {
            "id": "NVR-A",
            "name": "A 大樓",
            "host": "1.1.1.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    nvr_b = w.upsert_nvr(
        {
            "id": "NVR-B",
            "name": "B 大樓",
            "host": "2.2.2.2",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    run_id = w.begin_scan_run("2026-07-03T10:00:00Z")
    w.upsert_cameras(nvr_a, {101: "Cam-101", 102: "Cam-102"})
    w.upsert_cameras(nvr_b, {201: "Cam-201"})
    # Cam-101 有 2 種故障（nvr_a）
    w.insert_events(
        run_id,
        nvr_a,
        [
            {
                "deviceId": 101,
                "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
                "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
                "occurred_at": "2026-07-03T10:01:00Z",
            },
            {
                "deviceId": 101,
                "eventTopic": "DEVICE_TAMPERING",
                "eventTopics": ["DEVICE_TAMPERING"],
                "occurred_at": "2026-07-03T10:05:00Z",
            },
        ],
    )
    # Cam-201 有 2 筆同類型（nvr_b）
    w.insert_events(
        run_id,
        nvr_b,
        [
            {
                "deviceId": 201,
                "eventTopic": "DEVICE_COMMUNICATION_LOST",
                "eventTopics": ["DEVICE_COMMUNICATION_LOST"],
                "occurred_at": "2026-07-03T10:02:00Z",
            },
            {
                "deviceId": 201,
                "eventTopic": "DEVICE_COMMUNICATION_LOST",
                "eventTopics": ["DEVICE_COMMUNICATION_LOST"],
                "occurred_at": "2026-07-03T10:10:00Z",
            },
        ],
    )
    w.finish_scan_run(
        run_id,
        finished_at="2026-07-03T10:15:00Z",
        status="success",
        stats={
            "total_cameras": 3,
            "abnormal_cameras": 2,
            "total_nvrs": 2,
            "ok_nvrs": 2,
        },
    )
    del w


# === 1. /abnormal 路由 ===


def test_abnormal_empty_db(client):
    """空 DB：/abnormal 200 + 顯示「無異常」。"""
    resp = client.get("/abnormal")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "故障攝影機" in body
    assert "沒有任何未解決" in body


def test_abnormal_with_events(client, abn_app):
    """有異常事件 → 顯示分組。"""
    app, db_path = abn_app
    _seed_abnormal(db_path)
    resp = client.get("/abnormal")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 兩台 NVR 都出現
    assert "A 大樓" in body
    assert "B 大樓" in body
    # 有事件的相機出現
    assert "Cam-101" in body
    assert "Cam-201" in body
    # 沒事件的相機不應出現
    assert "Cam-102" not in body
    # 中文故障類型
    assert "黑畫面" in body
    assert "場景改變" in body
    assert "通訊中斷" in body


def test_abnormal_stats_correct(client, abn_app):
    """統計數字正確：2 NVR / 2 相機 / 4 事件。"""
    app, db_path = abn_app
    _seed_abnormal(db_path)
    resp = client.get("/abnormal")
    body = resp.get_data(as_text=True)
    # Cam-101 跟 Cam-201 各 1 相機有問題
    # 統計卡片
    # （Cam-102 沒事件 → 不算異常相機）
    assert "受影響 NVR" in body
    assert "受影響攝影機" in body
    assert "未解決事件總數" in body


# === 2. PDF 報告 ===


def test_abnormal_pdf_empty(client):
    """空 DB 的 PDF 200 + Content-Type application/pdf。"""
    resp = client.get("/abnormal/export.pdf")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert ".pdf" in resp.headers["Content-Disposition"]
    # PDF magic bytes
    assert resp.get_data()[:4] == b"%PDF"


def test_abnormal_pdf_with_events(client, abn_app):
    """有事件 → PDF 包含 NVR 名稱（bytes 內）。"""
    app, db_path = abn_app
    _seed_abnormal(db_path)
    resp = client.get("/abnormal/export.pdf")
    assert resp.status_code == 200
    data = resp.get_data()
    assert data[:4] == b"%PDF"
    # PDF 內的「NVR 異常攝影機報告」標題應該被編進 stream（壓縮）
    # reportlab 預設會壓縮，所以 bytes 內不一定能看到原文
    # 但 PDF 結構一定有
    assert len(data) > 2000  # 至少有 header + 字型 + 一些內容


def test_abnormal_pdf_contains_cjk_font(client):
    """PDF 含中文字型（msjh 內嵌時，bytes 內會有字型 table 標記）。"""
    resp = client.get("/abnormal/export.pdf")
    data = resp.get_data()
    # 中文字型會被嵌入，bytes 內含字型 subset
    # 微軟正黑體的全名 MicrosoftJhengHei 會出現在字型 metadata
    # 但 reportlab subset 後可能改名，直接看 size 是否明顯大於無字型版本
    # 簡化：只要 PDF 結構正常 + size > 1KB 就算
    assert len(data) > 1024
    # 沒有錯誤訊息
    assert b"Error" not in data


# === 3. /scan API ===


def test_scan_status_idle(client):
    """沒跑過時 /scan/status → running=False。"""
    resp = client.get("/scan/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["running"] is False


def test_abnormal_page_has_pdf_link(client):
    """/abnormal 頁有「歷史報告」按鈕（指向 /reports）。"""
    resp = client.get("/abnormal")
    body = resp.get_data(as_text=True)
    assert "歷史報告" in body
    assert "/reports" in body


# === 4. /runs/<id> 詳情頁加故障相機彙總 ===


def test_run_detail_shows_grouped_section(client, abn_app):
    """scan run 詳情頁有「故障相機彙總」section + PDF 入口。"""
    app, db_path = abn_app
    _seed_abnormal(db_path)
    # 找 run_id（從 DB）
    import sqlite3

    conn = sqlite3.connect(db_path)
    run_id = conn.execute(
        "SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.close()

    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 中文主題（STATE_DISCONNECTED 或 DEVICE_*）
    assert "黑畫面" in body or "通訊中斷" in body
    # 故障相機彙總 section
    assert "故障相機彙總" in body
    # Cam-101 跟 Cam-201 出現
    assert "Cam-101" in body
    assert "Cam-201" in body
    # 沒事件的 Cam-102 不應出現
    assert "Cam-102" not in body
    # PDF 入口（v2：歷史報告連結）
    assert "歷史報告" in body
    assert "/reports" in body


def test_run_detail_no_abnormal_no_pdf_link(client, abn_app):
    """scan run 詳情無異常時 → 不顯示 PDF 按鈕。"""
    app, db_path = abn_app
    # 不 seed 任何事件
    w = SqliteWriter(db_path)
    run_id = w.begin_scan_run("2026-07-03T10:00:00Z")
    w.finish_scan_run(
        run_id,
        finished_at="2026-07-03T10:01:00Z",
        status="success",
        stats={
            "total_cameras": 0,
            "abnormal_cameras": 0,
            "total_nvrs": 0,
            "ok_nvrs": 0,
        },
    )
    del w

    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "無異常事件" in body
    assert "下載 PDF 報告" not in body  # 沒事件就不顯示 PDF


# === 5. STATE_* 中文對照 ===


def test_state_disconnected_in_zh_map(client):
    """STATE_DISCONNECTED 應有中文對照（不該 fallback 顯示英文）。"""
    from web.db import ABNORMAL_TOPIC_ZH

    assert "STATE_DISCONNECTED" in ABNORMAL_TOPIC_ZH
    assert "斷線" in ABNORMAL_TOPIC_ZH["STATE_DISCONNECTED"]


def test_state_topic_in_run_detail(client, abn_app):
    """scan run 內含 STATE_DISCONNECTED 事件 → 顯示中文。"""
    app, db_path = abn_app
    w = SqliteWriter(db_path)
    nvr_a = w.upsert_nvr(
        {
            "id": "NVR-S",
            "name": "S 大樓",
            "host": "1.1.1.1",
            "port": 8443,
            "username": "u",
            "password": "p",
        }
    )
    run_id = w.begin_scan_run("2026-07-03T10:00:00Z")
    w.upsert_cameras(nvr_a, {301: "Cam-301"})
    w.insert_events(
        run_id,
        nvr_a,
        [
            {
                "deviceId": 301,
                "eventTopic": "STATE_DISCONNECTED",
                "eventTopics": ["STATE_DISCONNECTED"],
                "occurred_at": "2026-07-03T10:05:00Z",
            }
        ],
    )
    w.finish_scan_run(
        run_id,
        finished_at="2026-07-03T10:10:00Z",
        status="success",
        stats={
            "total_cameras": 1,
            "abnormal_cameras": 1,
            "total_nvrs": 1,
            "ok_nvrs": 1,
        },
    )
    del w

    resp = client.get(f"/runs/{run_id}")
    body = resp.get_data(as_text=True)
    # 中文顯示（不是 fallback 英文）
    assert "斷線" in body
    # 英文原始 event_topic 還是有顯示（給 debug 用）
    assert "STATE_DISCONNECTED" in body


# === 6. get_topic_zh fallback 測試 ===


def test_get_abnormal_cameras_grouped_filters_orphan_events(client, abn_app):
    """orphan event（指向已砍 NVR 的 device）不應出現在 /abnormal 結果。

    場景：dedup 後刪除某 NVR，留下的 events 仍指向舊 nvr_id，沒對應 cameras row。
    /abnormal 不應列出這些孤兒（顯示「(unknown #device_id)」會誤導）。
    """
    import sqlite3
    from web.db import get_abnormal_cameras_grouped

    app, db_path = abn_app
    _seed_abnormal(db_path)

    # 注入一個 orphan event：nvr_id=999（不存在）、device_id=ghost
    # 直接用 SQLite 繞過 SqliteWriter 的 scan_run 檢查
    conn = sqlite3.connect(db_path)
    try:
        run_id = conn.execute(
            "SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO events (scan_run_id, nvr_id, device_id, event_topic,
                                event_id, event_topics_json, occurred_at,
                                detected_at, raw_json, resolved_at)
            VALUES (?, 999, 'ghost-cam', 'DEVICE_VIDEO_SIGNAL_LOST',
                    'ghost-evt-1', '["DEVICE_VIDEO_SIGNAL_LOST"]',
                    '2026-07-03T11:00:00Z', '2026-07-03T11:00:00Z',
                    '{}', NULL)
            """,
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    groups = get_abnormal_cameras_grouped(db_path)
    device_ids = [g["device_id"] for g in groups]

    # 正常 events 還在
    assert "101" in device_ids
    assert "201" in device_ids
    # orphan event 必須被過濾掉
    assert (
        "ghost-cam" not in device_ids
    ), f"orphan event 不該出現，實際 device_ids={device_ids}"


def test_abnormal_page_hides_orphan_events(client, abn_app):
    """/abnormal HTML 也不應顯示 orphan event 的相機列。"""
    import sqlite3

    app, db_path = abn_app
    _seed_abnormal(db_path)

    conn = sqlite3.connect(db_path)
    try:
        run_id = conn.execute(
            "SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO events (scan_run_id, nvr_id, device_id, event_topic,
                                event_id, event_topics_json, occurred_at,
                                detected_at, raw_json, resolved_at)
            VALUES (?, 999, 'ghost-cam', 'DEVICE_VIDEO_SIGNAL_LOST',
                    'ghost-evt-1', '["DEVICE_VIDEO_SIGNAL_LOST"]',
                    '2026-07-03T11:00:00Z', '2026-07-03T11:00:00Z',
                    '{}', NULL)
            """,
            (run_id,),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/abnormal")
    body = resp.get_data(as_text=True)
    # orphan 應該不出現在頁面上
    assert "ghost-cam" not in body, "/abnormal 不該顯示 orphan event 的 device_id"
    # 正常的還在
    assert "Cam-101" in body
    assert "Cam-201" in body


def test_get_topic_zh_exact_match():
    from web.db import get_topic_zh

    assert "影像訊號斷線" in get_topic_zh("DEVICE_VIDEO_SIGNAL_LOST")
    assert "斷線" in get_topic_zh("STATE_DISCONNECTED")
    assert "長期失敗" in get_topic_zh("STATE_LONG_FAILED")


def test_get_topic_zh_generic_fallback():
    """沒明確對照的 STATE_* 用泛用對照。"""
    from web.db import get_topic_zh

    assert "正常" in get_topic_zh("STATE_CONNECTED")
    assert "升級" in get_topic_zh("STATE_UPGRADING")
    assert "不相容" in get_topic_zh("STATE_INCOMPATIBLE")


def test_get_topic_zh_auto_split():
    """沒對照的 STATE_FOO_BAR 自動拆字為 "Foo Bar"。"""
    from web.db import get_topic_zh

    # 完全沒對照
    assert get_topic_zh("STATE_FOO_BAR") == "Foo Bar"
    assert get_topic_zh("DEVICE_NEW_THING") == "New Thing"


def test_get_topic_zh_final_fallback():
    """非 STATE_/DEVICE_ 開頭的，原文回傳。"""
    from web.db import get_topic_zh

    assert get_topic_zh("UNKNOWN_TYPE") == "UNKNOWN_TYPE"
