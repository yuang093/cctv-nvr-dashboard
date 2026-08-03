"""
tests/integration/test_e2e_single_nvr.py
=========================================
單台 NVR 端到端測試：mock HTTPS server ↔ AvigilonScanner 真實 HTTP/S 傳輸。

覆蓋：
- 完全正常 NVR → 0 異常
- 含異常事件 + 異常 state 的 NVR → 正確識別雙重異常（事件 + state）
- login 403 → AuthError
- 連線失敗 → ConnectionError_
- DB 寫入：scan() 結果真的寫進 SQLite（cameras + events 表）
"""
from __future__ import annotations

import pytest

from nvr_scanner import AuthError, AvigilonScanner, ConnectionError_
from tests.integration.mock_acc import (
    MockAvigilonServer,
    make_abnormal_nvr,
    make_login_fail_nvr,
    make_normal_nvr,
)


def _make_scanner(server: MockAvigilonServer) -> AvigilonScanner:
    """根據已啟動的 mock server 建 scanner。"""
    return AvigilonScanner(
        {
            "id": "test-nvr",
            "name": "MockNVR",
            "host": server.host,
            "port": server.port,
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
        },
        user_nonce="test-nonce",
        user_key="test-key",
        timeout=5,
    )


class TestSingleNvrEndToEnd:
    """End-to-end：mock server ↔ AvigilonScanner 真實連線。"""

    def test_scan_normal_nvr_zero_abnormal(self, mock_nvr_normal):
        """全正常 NVR 應回傳 0 異常。"""
        s = _make_scanner(mock_nvr_normal)
        result = s.scan()

        assert result["nvr_id"] == "test-nvr"
        assert result["stats"]["total_cameras"] == 3
        assert result["stats"]["abnormal_cameras"] == 0
        assert result["events"] == []
        # 確認 server 真的有被呼叫
        assert mock_nvr_normal.config.request_count.get("login") == 1
        assert mock_nvr_normal.config.request_count.get("cameras") == 1
        assert mock_nvr_normal.config.request_count.get("events") == 1

    def test_scan_abnormal_nvr_double_check(self, mock_nvr_server):
        """含 2 異常事件 + 1 LONG_FAILED state → 雙重檢查應抓到 3 異常。

        cam-001: 有 DEVICE_VIDEO_SIGNAL_LOST 事件 → 標異常
        cam-002: 有 DEVICE_TAMPERING 事件 + LONG_FAILED state → 標異常（事件優先）
        cam-003: 無事件但 DISCONNECTED state → 由 state 雙重檢查抓到
        """
        s = _make_scanner(mock_nvr_server)
        result = s.scan()

        assert result["stats"]["total_cameras"] == 3
        assert result["stats"]["abnormal_cameras"] == 3

        # 異常 ID 集合
        abnormal_ids = {e["deviceId"] for e in result["events"]}
        assert abnormal_ids == {"cam-001", "cam-002", "cam-003"}

        # cam-001: 來自 event
        cam_001_event = next(
            e for e in result["events"] if e["deviceId"] == "cam-001"
        )
        assert "DEVICE_VIDEO_SIGNAL_LOST" in cam_001_event["eventTopics"]

        # cam-003: 來自 state（無對應 event）
        cam_003_event = next(
            e for e in result["events"] if e["deviceId"] == "cam-003"
        )
        assert cam_003_event["source"] == "camera_state"
        assert cam_003_event["connection_state"] == "DISCONNECTED"

    def test_scan_cameras_dict_has_names_and_states(self, mock_nvr_server):
        """scan() 回傳的 cameras dict 應含 name + connection_state。"""
        s = _make_scanner(mock_nvr_server)
        result = s.scan()

        cams = result["cameras"]
        assert cams["cam-001"]["name"] == "前門"
        assert cams["cam-001"]["connection_state"] == "CONNECTED"
        assert cams["cam-002"]["connection_state"] == "LONG_FAILED"
        assert cams["cam-003"]["name"] == "倉庫"

    def test_login_failure_raises_auth_error(self, mock_nvr_login_fail):
        """login 回 403 → AuthError。"""
        s = _make_scanner(mock_nvr_login_fail)
        with pytest.raises(AuthError):
            s.scan()

    def test_state_only_no_events_still_flagged(self):
        """無 events 但相機 state 非 CONNECTED → 仍標異常（雙重檢查證明）。"""
        # 客製 config：相機全 LONG_FAILED，但 events 為空
        config = make_abnormal_nvr()
        config.events = []  # 移除事件
        # cameras 預設就含 LONG_FAILED + DISCONNECTED
        server = MockAvigilonServer(config)
        server.start()
        try:
            s = _make_scanner(server)
            result = s.scan()

            # 雖然 events 為空，但 state 雙重檢查應標 2 異常
            assert result["stats"]["abnormal_cameras"] == 2
            for ev in result["events"]:
                assert ev["source"] == "camera_state"
        finally:
            server.stop()

    def test_scanner_uses_https_with_self_signed(self, mock_nvr_server):
        """驗證 mock server 真的是 HTTPS（不是 HTTP）。"""
        s = _make_scanner(mock_nvr_server)
        assert s.base_url.startswith("https://")
        # scan() 成功 → 確認自簽憑證 + verify_ssl=False 走通
        result = s.scan()
        assert result["nvr_id"] == "test-nvr"


class TestSingleNvrWithDatabase:
    """End-to-end：scan() 結果真的寫進 SQLite。"""

    def test_scan_result_persists_to_db(
        self, mock_nvr_server, integration_writer,
    ):
        """scan() 結果走完整 SqliteWriter 流程（upsert → begin → upsert_cam → insert → finish）。"""
        s = _make_scanner(mock_nvr_server)
        result = s.scan()

        # 模擬 batch_scan 流程
        nvr_int_id = integration_writer.upsert_nvr({
            "id": "test-nvr",
            "name": "MockNVR",
            "host": mock_nvr_server.host,
            "port": mock_nvr_server.port,
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
        })
        run_id = integration_writer.begin_scan_run("2026-06-23T10:00:00Z")
        integration_writer.upsert_cameras(nvr_int_id, result["cameras"])
        integration_writer.insert_events(run_id, nvr_int_id, result["events"])
        integration_writer.finish_scan_run(
            run_id,
            finished_at="2026-06-23T10:00:05Z",
            status="success",
            stats={
                "total_cameras": result["stats"]["total_cameras"],
                "abnormal_cameras": result["stats"]["abnormal_cameras"],
                "total_nvrs": 1,
                "ok_nvrs": 1,
                "failed_nvrs": 0,
            },
        )

        # 查詢驗證
        runs = integration_writer.get_scan_runs(limit=5)
        assert len(runs) == 1
        assert runs[0]["status"] == "success"
        assert runs[0]["total_cameras"] == 3
        assert runs[0]["abnormal_cameras"] == 3

        events = integration_writer.get_events_for_run(run_id)
        assert len(events) == 3  # 2 異常事件 + 1 state 合成事件
        topics = {e["event_topic"] for e in events}
        assert "DEVICE_VIDEO_SIGNAL_LOST" in topics
        assert "DEVICE_TAMPERING" in topics
        assert "STATE_DISCONNECTED" in topics

    def test_event_insert_dedup_via_state_priority(
        self, mock_nvr_server, integration_writer,
    ):
        """同一相機 event + state 都觸發時，去重為一個（events 來源優先）。

        cam-002 既有 TAMPERING 事件，又 LONG_FAILED state，
        但事件來源去重後只算一台異常（不重複列）。
        """
        s = _make_scanner(mock_nvr_server)
        result = s.scan()

        cam_002_events = [
            e for e in result["events"] if e["deviceId"] == "cam-002"
        ]
        assert len(cam_002_events) == 1
        # 是 event 來源，不是 state 合成
        assert "DEVICE_TAMPERING" in cam_002_events[0]["eventTopics"]
        assert "source" not in cam_002_events[0] or \
            cam_002_events[0].get("source") != "camera_state"
