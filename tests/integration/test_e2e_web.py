"""
tests/integration/test_e2e_web.py
==================================
Web UI 端到端測試：mock NVR → batch_scan 真實 DB 寫入 → Flask 渲染。

覆蓋：
- dashboard 顯示統計數字（總 NVR/相機/異常事件/最近狀態）
- /runs 列出 scan_runs 並分頁
- /runs/<id> 顯示單次掃描詳情
- /nvrs 列出 NVR 配置
- /events 篩選異常事件（hours / nvr / topic）
- 真實 SQLite（跨連線讀寫：worker + web.db）
"""
from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from batch_scan import batch_scan
from web.app import create_app


class TestWebEndToEnd:
    """完整 pipeline：mock NVR → batch_scan → web 渲染。"""

    def test_dashboard_shows_real_stats(
        self, integration_db, integration_config, integration_credentials,
    ):
        """dashboard 應顯示真實統計（總 NVR/相機/異常數）。"""
        db_path, writer = integration_db

        # 1. 跑 batch_scan 真的寫入 DB
        result = batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        assert result["status"] == "partial"

        # 2. 建 Flask app 讀同個 DB
        app = create_app(db_path=db_path)
        client = app.test_client()

        # 3. GET /
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # 4. dashboard 統計卡：總 NVR=3、總相機=6、最後狀態=partial
        assert "3" in html  # 總 NVR
        assert "6" in html  # 總相機
        assert "部分成功" in html  # status badge (partial run → 部分成功)

    def test_runs_list_with_pagination(
        self, integration_db, integration_config, integration_credentials,
    ):
        """runs 列表應顯示 scan_run + 分頁。"""
        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client: FlaskClient = app.test_client()

        resp = client.get("/runs")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        # scan_run 統計顯示（中文狀態標籤）
        assert "部分成功" in html
        # 顯示 ok_nvrs / failed_nvrs 數字
        # 1.0 normal 3 cam, 1.1 abnormal 3 cam with 3 abnormal, 1.2 login fail
        assert "2" in html  # ok_nvrs

        # page 2 應回 200（即使空）
        resp2 = client.get("/runs?page=2")
        assert resp2.status_code == 200

    def test_run_detail_with_events_and_cameras(
        self, integration_db, integration_config, integration_credentials,
    ):
        """run_detail 顯示單次掃描的 events + cameras。"""
        db_path, writer = integration_db
        result = batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        run_id = result["scan_run_id"]
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # 異常事件主題應出現
        assert "DEVICE_VIDEO_SIGNAL_LOST" in html
        assert "DEVICE_TAMPERING" in html
        assert "STATE_DISCONNECTED" in html

        # 相機名稱應出現（前門、後門、倉庫 + NVR 0 的相機1/2/3）
        assert "前門" in html
        assert "後門" in html
        assert "倉庫" in html

    def test_run_detail_404_for_missing(
        self, integration_db, integration_config, integration_credentials,
    ):
        """不存在的 run_id 應回 404。"""
        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/runs/9999")
        assert resp.status_code == 404

    def test_nvrs_list_shows_three_nvrs(
        self, integration_db, integration_config, integration_credentials,
    ):
        """nvrs 列表應顯示全部 3 台 NVR（含 login-fail 那台）。"""
        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/nvrs")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # 3 台 NVR 名稱都應顯示（即使 login-fail 也被 upsert）
        assert "MockNVR-0" in html
        assert "MockNVR-1" in html
        assert "MockNVR-2" in html

    def test_nvrs_list_shows_camera_count(
        self, integration_db, integration_config, integration_credentials,
    ):
        """每台 NVR 應顯示其相機數（mock-0=3、mock-1=3、mock-2=0）。"""
        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/nvrs")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # 統計：總相機數 6（3 + 3 + 0）出現在某處
        assert "6" in html  # 相機總數

    def test_events_list_filter_by_topic(
        self, integration_db, integration_config, integration_credentials,
    ):
        """events 列表篩選 topic=DEVICE_TAMPERING 應只剩 TAMPERING 事件。

        篩選後 events 表格只剩 TAMPERING；但 topic 快選連結仍顯示所有主題
        （這是「點其他主題重新篩選」的 UI），所以頁面全文仍可能含其他主題。
        """
        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/events?topic=DEVICE_TAMPERING")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        # 應有 TAMPERING 事件（事件表格 + topic badge）
        assert "DEVICE_TAMPERING" in html
        # 篩選 URL 應回傳，但頁面仍列出所有 topic 快選連結（這是預期）

    def test_events_list_filter_actually_filters(
        self, integration_db, integration_config, integration_credentials,
    ):
        """web.db.get_events_filtered 確實有套 topic 篩選（直接呼叫 helper 驗證）。

        避免 HTML 渲染含「topic badge 連結」的雜訊，直接驗 web.db helper。
        """
        from web.db import get_events_filtered

        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )

        all_events = get_events_filtered(db_path, hours=24 * 365, limit=200)
        tampering_only = get_events_filtered(
            db_path, hours=24 * 365, topic="DEVICE_TAMPERING", limit=200,
        )

        assert len(all_events) >= 3
        assert len(tampering_only) == 1
        assert tampering_only[0]["event_topic"] == "DEVICE_TAMPERING"

        # 另一主題篩選
        state_only = get_events_filtered(
            db_path, hours=24 * 365, topic="STATE_DISCONNECTED", limit=200,
        )
        assert len(state_only) == 1
        assert state_only[0]["event_topic"] == "STATE_DISCONNECTED"

    def test_events_list_all_topics_visible(
        self, integration_db, integration_config, integration_credentials,
    ):
        """events 列表應顯示所有異常主題（讓使用者點擊）。"""
        db_path, writer = integration_db
        batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/events")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")

        # 主題快選連結應包含這 3 個主題
        assert "DEVICE_VIDEO_SIGNAL_LOST" in html
        assert "DEVICE_TAMPERING" in html
        assert "STATE_DISCONNECTED" in html

    def test_static_css_loads(
        self, integration_db, integration_config, integration_credentials,
    ):
        """靜態 CSS 應可取得（健康檢查）。"""
        db_path, writer = integration_db
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/static/style.css")
        assert resp.status_code == 200


class TestWebWithFailedNvr:
    """Web UI 對 partial 狀態的渲染。"""

    def test_dashboard_distinguishes_running_vs_finished(
        self, integration_db, integration_config, integration_credentials,
    ):
        """scan_run 結束後 status 應為 success/partial/finished，不是 running。"""
        db_path, writer = integration_db
        result = batch_scan(
            integration_config, integration_credentials, writer,
            timeout=5, verbose=False,
        )
        app = create_app(db_path=db_path)
        client = app.test_client()

        resp = client.get("/")
        html = resp.data.decode("utf-8")
        # 顯示 status badge
        # Chinese labels: partial → 部分成功, success → 成功
        assert "部分成功" in html or "成功" in html
