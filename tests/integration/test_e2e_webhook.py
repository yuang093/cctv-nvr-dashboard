"""
tests/integration/test_e2e_webhook.py
======================================
Webhook 端到端測試：batch_scan 真實執行後 → webhook 真實 POST 到 mock receiver。

驗證：
- abnormal > 0 時真的送出
- abnormal = 0 時不送
- POST 內容含正確的 Slack / Teams payload
- 接收器回 500 不影響 batch_scan 完成
- 多 webhook URL 都收到
"""
from __future__ import annotations

import os

import pytest

from batch_scan import batch_scan
from tests.integration.mock_acc import (
    MockAvigilonServer,
    MockWebhookReceiver,
    make_abnormal_nvr,
    make_login_fail_nvr,
    make_normal_nvr,
)


def _make_config_with_webhooks(nvr_configs, webhook_configs):
    """組合出 batch_scan 用的 config（含 webhooks 段）。"""
    return {
        "scan_settings": {"db_path": ":memory:", "timeout_seconds": 5},
        "nvr_servers": nvr_configs,
        "webhooks": webhook_configs,
    }


def _make_nvr_config(nvr_id, server):
    return {
        "id": nvr_id,
        "name": f"MockNVR-{nvr_id}",
        "host": server.host,
        "port": server.port,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
        "enabled": True,
    }


@pytest.fixture
def mock_nvr_abnormal_only(flush_urllib3_warnings):
    """1 台異常 NVR（用於 webhook 有事可報的情境）。"""
    server = MockAvigilonServer(make_abnormal_nvr())
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_nvr_normal_only(flush_urllib3_warnings):
    """1 台全正常 NVR（用於 webhook 不觸發的情境）。"""
    server = MockAvigilonServer(make_normal_nvr(camera_count=3))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def webhook_receiver():
    """接收 webhook POST 的 mock HTTP server。"""
    receiver = MockWebhookReceiver()
    receiver.start()
    yield receiver
    receiver.stop()


class TestWebhookEndToEnd:
    """完整的 webhook 推送 pipeline：NVR → batch_scan → webhook.py → mock receiver。"""

    def test_abnormal_triggers_slack_webhook(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        webhook_receiver,
        integration_credentials,
    ):
        """abnormal > 0 → batch_scan 送出 webhook，receiver 收到正確的 Slack payload。"""
        _, writer = integration_db
        config = _make_config_with_webhooks(
            [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
            [{
                "provider": "slack",
                "url": webhook_receiver.base_url + "/slack/hook",
                "channel": "#test",
                "enabled": True,
            }],
        )

        result = batch_scan(
            config, integration_credentials, writer,
            timeout=5, verbose=False,
        )

        assert result["status"] == "success"  # 1 台就過
        assert result["abnormal_cameras"] == 3

        # receiver 應該收到 1 筆 POST
        assert len(webhook_receiver.received) == 1
        received = webhook_receiver.received[0]
        assert received["path"] == "/slack/hook"
        body = received["decoded"]
        assert body is not None
        # Slack payload 結構
        assert "blocks" in body
        assert body.get("channel") == "#test"
        # 訊息內含異常摘要
        text = body["blocks"][0]["text"]["text"]
        assert "partial" in text or "success" in text
        assert "cam-001" in text or "DEVICE_VIDEO_SIGNAL_LOST" in text

    def test_abnormal_triggers_teams_webhook(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        webhook_receiver,
        integration_credentials,
    ):
        """abnormal > 0 → Teams MessageCard payload。"""
        _, writer = integration_db
        config = _make_config_with_webhooks(
            [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
            [{
                "provider": "teams",
                "url": webhook_receiver.base_url + "/teams/hook",
                "enabled": True,
            }],
        )

        batch_scan(
            config, integration_credentials, writer, timeout=5, verbose=False,
        )

        assert len(webhook_receiver.received) == 1
        body = webhook_receiver.received[0]["decoded"]
        assert body["@type"] == "MessageCard"
        assert "@context" in body
        # themeColor 應對應 run status
        assert body["themeColor"] in ("FF0000", "FFA500", "00FF00")
        assert "sections" in body

    def test_no_abnormal_skips_webhook(
        self,
        integration_db,
        mock_nvr_normal_only,
        webhook_receiver,
        integration_credentials,
    ):
        """abnormal = 0 → 不送 webhook。"""
        _, writer = integration_db
        config = _make_config_with_webhooks(
            [_make_nvr_config("nvr-1", mock_nvr_normal_only)],
            [{
                "provider": "slack",
                "url": webhook_receiver.base_url + "/should-not-fire",
                "enabled": True,
            }],
        )

        result = batch_scan(
            config, integration_credentials, writer, timeout=5, verbose=False,
        )

        assert result["abnormal_cameras"] == 0
        # webhook 不應被觸發
        assert len(webhook_receiver.received) == 0

    def test_receiver_500_does_not_break_batch(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        integration_credentials,
    ):
        """webhook receiver 回 500 → batch_scan 仍標 success。"""
        # 用獨立 receiver（避免 port conflict）
        receiver = MockWebhookReceiver(response_status=500, response_body="boom")
        receiver.start()

        try:
            _, writer = integration_db
            config = _make_config_with_webhooks(
                [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
                [{
                    "provider": "slack",
                    "url": receiver.base_url + "/fail",
                    "enabled": True,
                }],
            )

            result = batch_scan(
                config, integration_credentials, writer, timeout=5, verbose=False,
            )

            # batch_scan 本身仍應 success（webhook 失敗不影響）
            assert result["status"] == "success"
            # 但 webhook_results 應記錄失敗
            assert len(result["webhook_results"]) == 1
            assert result["webhook_results"][0]["success"] is False
            assert "500" in result["webhook_results"][0]["error"]
        finally:
            receiver.stop()

    def test_multiple_webhooks_all_sent(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        integration_credentials,
    ):
        """多個 webhook 都收到（2 receiver + 不同 provider）。"""
        recv_slack = MockWebhookReceiver()
        recv_teams = MockWebhookReceiver()
        recv_slack.start()
        recv_teams.start()

        try:
            _, writer = integration_db
            config = _make_config_with_webhooks(
                [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
                [
                    {"provider": "slack", "url": recv_slack.base_url + "/s"},
                    {"provider": "teams", "url": recv_teams.base_url + "/t"},
                ],
            )
            result = batch_scan(
                config, integration_credentials, writer, timeout=5, verbose=False,
            )
            assert len(recv_slack.received) == 1
            assert len(recv_teams.received) == 1
            # 兩個都成功
            assert all(r["success"] for r in result["webhook_results"])
        finally:
            recv_slack.stop()
            recv_teams.stop()

    def test_disabled_webhook_skipped(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        webhook_receiver,
        integration_credentials,
    ):
        """enabled=False → 不送。"""
        _, writer = integration_db
        config = _make_config_with_webhooks(
            [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
            [{
                "provider": "slack",
                "url": webhook_receiver.base_url + "/disabled",
                "enabled": False,
            }],
        )

        batch_scan(
            config, integration_credentials, writer, timeout=5, verbose=False,
        )

        assert len(webhook_receiver.received) == 0

    def test_env_var_substitution_for_url(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        webhook_receiver,
        integration_credentials,
        monkeypatch,
    ):
        """URL 含 ${ENV_VAR} → 從環境變數取得。"""
        monkeypatch.setenv("TEST_WEBHOOK_URL", webhook_receiver.base_url + "/env")
        _, writer = integration_db
        config = _make_config_with_webhooks(
            [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
            [{
                "provider": "slack",
                "url": "${TEST_WEBHOOK_URL}",
                "enabled": True,
            }],
        )

        batch_scan(
            config, integration_credentials, writer, timeout=5, verbose=False,
        )

        assert len(webhook_receiver.received) == 1
        assert webhook_receiver.received[0]["path"] == "/env"

    def test_unset_env_var_skips_silently(
        self,
        integration_db,
        mock_nvr_abnormal_only,
        integration_credentials,
        monkeypatch,
    ):
        """環境變數未設定 → URL 替換為空 → 跳過該 webhook。"""
        monkeypatch.delenv("UNSET_WEBHOOK_URL_XYZ", raising=False)
        _, writer = integration_db
        config = _make_config_with_webhooks(
            [_make_nvr_config("nvr-1", mock_nvr_abnormal_only)],
            [{
                "provider": "slack",
                "url": "${UNSET_WEBHOOK_URL_XYZ}",
                "enabled": True,
            }],
        )

        result = batch_scan(
            config, integration_credentials, writer, timeout=5, verbose=False,
        )

        # batch_scan 正常結束
        assert result["status"] == "success"
        # webhook 標記失敗（空 URL），不丟例外
        assert len(result["webhook_results"]) == 1
        assert result["webhook_results"][0]["success"] is False
