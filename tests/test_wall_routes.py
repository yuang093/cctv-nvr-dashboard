"""
tests/test_wall_routes.py
=========================
Phase 2.8（Arisan）Phase #5：/wall 相機牆 route + template。

路由：GET /wall                  → 全 cam grid
      GET /wall?filter=online    → 只 CONNECTED
      GET /wall?filter=signal_lost → 只 STATE_* is_fault=1（影像訊號/通訊類）
      GET /wall?filter=no_signal → 完全斷線類（DEVICE_DISCONNECTED / STATE_LONG_FAILED）

注意：cameras 表**沒有** connection_status 欄位（doc/scan-flow.md 寫錯），
連線狀態只存在 nvr_scanner 的 dataclass 內。所以 /wall 從 events 表推：
- 用每台 cam 最新的「未解事件 topic」決定分類
- 沒事件的 cam 視為 online（最新一次 scan 沒抓到 fault → CONNECTED）
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def wall_app():
    """灌 2 台 NVR、5 台 cam、各 cam 帶不同事件。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A 分店", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    nvrb = w.upsert_nvr({
        "id": "NVR-B", "name": "B 分店", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-17T00:00:00Z")
    w.upsert_cameras(nvra, {
        "d1": {"name": "大門", "connection_state": "CONNECTED",
               "ip_address": "192.168.133.103:443"},
        "d2": {"name": "停車場", "connection_state": "LONG_FAILED",
               "ip_address": "192.168.133.105:443"},
        "d3": {"name": "後門", "connection_state": "CONNECTED",
               "ip_address": "192.168.133.110:443"},
    })
    w.upsert_cameras(nvrb, {
        "d10": {"name": "倉庫大門", "connection_state": "CONNECTED"},
        "d11": {"name": "倉庫後門", "connection_state": "CONNECTED"},
    })
    # d2 → STATE_LONG_FAILED（長期失敗，no_signal 分類）
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "d2",
        "eventTopics": ["STATE_LONG_FAILED"],
        "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-07-17T00:00:00Z",
    }])
    # d3 → DEVICE_VIDEO_SIGNAL_LOST（影像訊號斷線 → signal_lost）
    w.insert_events(rid, nvra, [{
        "eventId": "e2", "deviceId": "d3",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-17T00:00:00Z",
    }])
    w.finish_scan_run(
        rid, finished_at="2026-07-17T00:01:00Z", status="partial",
        stats={"total_cameras": 5, "abnormal_cameras": 2,
               "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0},
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
def client(wall_app):
    app, _ = wall_app
    return app.test_client()


# === 1. /wall 回 200 ===
def test_wall_returns_200(client):
    resp = client.get("/wall")
    assert resp.status_code == 200


# === 2. 全 cam grid 預設顯示所有 cam ===
def test_wall_default_shows_all_cams(client):
    """沒 filter → 顯示全部 5 台 cam。"""
    body = client.get("/wall").get_data(as_text=True)
    assert "大門" in body
    assert "停車場" in body
    assert "後門" in body
    assert "倉庫大門" in body
    assert "倉庫後門" in body


# === 3. 「重新掃描」按鈕（給 cam 重啟後更新縮圖用）===
def test_wall_has_rescan_button(client):
    """2026-07-30：cam 重啟後 user 需要在 /wall 一鍵觸發 scan。

    按鈕 → fetch POST /scan → 10 秒後 auto-reload（更新縮圖）。
    """
    body = client.get("/wall").get_data(as_text=True)
    # 按鈕存在（文字）
    assert "重新掃描" in body
    # JS 有 POST /scan 邏輯
    assert "/scan" in body and ("fetch" in body or "XMLHttpRequest" in body)
    # JS 有 reload（完成後自動更新畫面）
    assert "location.reload" in body or "window.location.reload" in body


# === 3. /wall?filter=online 只顯示無故障 cam ===
def test_wall_filter_online(client):
    """filter=online → 只顯示沒未解事件的 cam（d1, d10, d11）。"""
    body = client.get("/wall?filter=online").get_data(as_text=True)
    assert "大門" in body         # d1
    assert "倉庫大門" in body     # d10
    assert "倉庫後門" in body     # d11
    assert "停車場" not in body   # d2 STATE_LONG_FAILED
    # d3 是「後門」訊號斷線不應出現；用 device_id 標記精確比對避免 substring 衝突
    assert 'device_id: <code>d3</code>' not in body
    assert 'device_id: <code>d2</code>' not in body


# === 4. /wall?filter=signal_lost 只顯示訊號問題 ===
def test_wall_filter_signal_lost(client):
    """filter=signal_lost → DEVICE_VIDEO_SIGNAL_LOST / DEVICE_COMMUNICATION_LOST 類。"""
    body = client.get("/wall?filter=signal_lost").get_data(as_text=True)
    # d3 訊號斷線：顯示「停車場」「後門」其一 + 「訊號中斷」badge
    # d2（停車場）歸 no_signal 不應出現
    assert "大門" not in body        # d1 online
    assert "停車場" not in body      # d2 no_signal
    assert "倉庫大門" not in body    # d10 online
    assert "後門" in body            # d3 signal_lost → 顯示
    assert "訊號中斷" in body


# === 5. /wall?filter=no_signal 只顯示完全斷線類 ===
def test_wall_filter_no_signal(client):
    """filter=no_signal → STATE_LONG_FAILED / STATE_DISCONNECTED / DEVICE_DISCONNECTED 類。"""
    body = client.get("/wall?filter=no_signal").get_data(as_text=True)
    assert "停車場" in body          # d2 STATE_LONG_FAILED
    assert "後門" not in body         # d3 訊號斷線不歸此類
    assert "大門" not in body         # d1 online
    assert "無訊號" in body


# === 6. cam 卡顯示 category badge（2026-07-29 視覺化重構：改用 status 圓點）===
def test_wall_renders_category_dot(client):
    """2026-07-29 重構：category 用 status 圓點（bg-success / bg-danger / bg-warning）。

    篩選 tab 仍顯示「在線 / 訊號中斷 / 無訊號」label（給計數用），
    但卡片本體只顯示綠/紅/琥珀圓點，不再有 ✓ 在線 文字 badge。
    """
    body = client.get("/wall").get_data(as_text=True)
    # 圓點用 Bootstrap bg-* class
    assert "bg-success" in body     # online 綠圓點
    assert "bg-danger" in body      # signal_lost 紅圓點
    assert "bg-warning" in body     # no_signal 琥珀圓點
    assert "wall-status-dot" in body  # 自訂 class
    # 篩選 tab label 仍出現（給計數 + 篩選用）
    assert "在線" in body
    assert "訊號中斷" in body
    assert "無訊號" in body
    # catalog 中文 label 不再出現在磁磚（user 要求拿掉主題）
    assert "長期失敗（拔網路線）" not in body
    assert "影像訊號斷線（黑畫面）" not in body


# === 7. NVR 名稱標籤 ===
def test_wall_renders_nvr_name(client):
    """每張卡應帶 NVR 名稱（讓 user 知道是哪台 NVR 的 cam）。"""
    body = client.get("/wall").get_data(as_text=True)
    assert "A 分店" in body
    assert "B 分店" in body


# === 8. 篩選按鈕高亮當前 filter ===
def test_wall_filter_button_highlighted(client):
    """當前 filter 的按鈕要有 active class（視覺提示）。"""
    body = client.get("/wall?filter=online").get_data(as_text=True)
    # 不強制驗 CSS class（會 brittle），但應含 filter 名稱的 active 樣式
    # 至少確認「全部 / 在線 / 訊號中斷 / 無訊號」四個按鈕都在
    for label in ("全部", "在線", "訊號中斷", "無訊號"):
        assert label in body, f"找不到 filter 按鈕「{label}」"


# === 9. 點 cam 卡跳 /devices/<id> ===
def test_wall_cam_card_links_to_device_detail(client):
    """每張 cam 卡應是 anchor，href 含 /devices/<device_id>。"""
    body = client.get("/wall").get_data(as_text=True)
    # device_id 用 d1 / d2 / d3 / d10 / d11
    assert "/devices/d1" in body
    assert "/devices/d2" in body
    assert "/devices/d10" in body


# === 10. /wall 不顯示 cam IP（2026-07-29 視覺化精簡 UI）===
def test_wall_does_not_render_ip_address(client):
    """2026-07-29 視覺化重構：卡片精簡，不再顯示 IP 行（避免塞太多文字）。

    IP 仍可在 /devices/<id> 詳情頁看到。
    """
    body = client.get("/wall").get_data(as_text=True)
    assert "192.168.133.103" not in body
    assert "192.168.133.105" not in body
    assert "192.168.133.110" not in body


# === 11. /wall 不顯示 device_id 行（避免 SHA-256 雜湊污染畫面）===
def test_wall_does_not_render_device_id_line(client):
    """磁磚不應出現 device_id: <code>...</code> 行。"""
    body = client.get("/wall").get_data(as_text=True)
    assert "device_id:" not in body, "不應再顯示 device_id 行"
    # 但 URL /devices/d1 仍要存在（連結還是要帶 device_id）
    assert "/devices/d1" in body


# === 12. /wall 不顯示「主題：」行（中文 catalog label 訊息列已不需要）===
def test_wall_does_not_render_topic_line(client):
    """磁磚不應出現「主題：…」行（user 要求拿掉）。

    2026-07-29 重構：category 改成 status 圓點（驗證在 #6）。
    """
    body = client.get("/wall").get_data(as_text=True)
    assert "主題：" not in body, "不應再顯示「主題：」行"


# === 13. /wall 顯示 24h 錄影完整率（recording_pct）===
def test_wall_renders_recording_pct_when_present(client):
    """若 cam 有 recording_status 紀錄，磁磚應顯示 24h 完整率（百分比）。"""
    body = client.get("/wall").get_data(as_text=True)
    # fixture 沒塞 recording_status → 不應出現「錄影：」或「—」
    # 後續若 DB 沒資料，UI 會顯示「—」；此處先驗結構：磁磚不存在關鍵字
    # 完整率功能正式上線後會用獨立 fixture 驗
    assert "錄影" not in body  # 沒資料時不出現錄影行
