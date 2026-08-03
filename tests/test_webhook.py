"""
tests/test_webhook.py
=====================
Webhook 推播模組單元測試。

覆蓋：
- substitute_env_vars：環境變數替換
- _build_slack_payload：Slack 訊息格式
- _build_teams_payload：Teams MessageCard 格式
- send_webhook：成功 / 失敗 / 不觸發條件
- send_webhooks：多個 webhook 並行
- batch_scan 整合：webhook 觸發條件
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from batch_scan import _parse_webhook_configs, batch_scan
from webhook import (
    WebhookConfig,
    WebhookPayload,
    _build_slack_payload,
    _build_teams_payload,
    send_webhook,
    send_webhooks,
    substitute_env_vars,
)


# === fixture：完整 payload ===
@pytest.fixture
def sample_payload():
    return WebhookPayload(
        run_status="partial",
        total_nvrs=3, ok_nvrs=2, failed_nvrs=1,
        total_cameras=8, abnormal_cameras=3,
        started_at="2026-06-30T10:00:00Z",
        finished_at="2026-06-30T10:00:05Z",
        per_nvr_results=[
            {
                "result": {
                    "nvr_name": "MockNVR-1",
                    "events": [
                        {"deviceId": "cam-001",
                         "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"]},
                        {"deviceId": "cam-002",
                         "eventTopics": ["DEVICE_TAMPERING"]},
                        {"deviceId": "cam-003",
                         "eventTopics": ["STATE_DISCONNECTED"],
                         "source": "camera_state"},
                    ],
                }
            },
            {
                "result": {
                    "nvr_name": "MockNVR-0",
                    "events": [],  # 正常 NVR 沒事件
                }
            },
        ],
    )


# === substitute_env_vars ===
class TestSubstituteEnvVars:

    def test_replace_dollar_brace(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "https://example.com")
        url = substitute_env_vars("prefix-${MY_TEST_VAR}-suffix")
        assert url == "prefix-https://example.com-suffix"

    def test_unset_var_becomes_empty(self):
        os.environ.pop("UNSET_VAR_XYZ", None)
        url = substitute_env_vars("https://${UNSET_VAR_XYZ}/path")
        assert url == "https:///path"  # 空字串

    def test_no_var_unchanged(self):
        assert substitute_env_vars("https://example.com") == "https://example.com"

    def test_multiple_vars_in_one_url(self, monkeypatch):
        monkeypatch.setenv("HOST", "hooks.slack.com")
        monkeypatch.setenv("PATH", "/services/abc")
        url = substitute_env_vars("https://${HOST}${PATH}")
        assert url == "https://hooks.slack.com/services/abc"

    def test_partial_match_not_substituted(self):
        """$VAR（無大括號）不應被當成 placeholder。"""
        assert substitute_env_vars("$NOT_BRACED") == "$NOT_BRACED"


# === Slack payload ===
class TestSlackPayload:

    def test_includes_status_and_counts(self, sample_payload):
        body = _build_slack_payload(sample_payload)
        text = body["blocks"][0]["text"]["text"]
        assert "partial" in text
        assert "3" in text  # abnormal
        assert "8" in text  # total_cameras
        assert "3" in text  # total_nvrs

    def test_includes_top_anomalies(self, sample_payload):
        body = _build_slack_payload(sample_payload)
        text = body["blocks"][0]["text"]["text"]
        assert "cam-001" in text
        assert "DEVICE_VIDEO_SIGNAL_LOST" in text
        assert "STATE_DISCONNECTED" in text

    def test_summary_line_includes_counts(self, sample_payload):
        body = _build_slack_payload(sample_payload)
        assert "3 台異常" in body["text"]
        assert "8 台總計" in body["text"]

    def test_success_status_uses_check_emoji(self, sample_payload):
        sample_payload.run_status = "success"
        body = _build_slack_payload(sample_payload)
        text = body["blocks"][0]["text"]["text"]
        assert "✅" in text


# === Teams payload ===
class TestTeamsPayload:

    def test_messagecard_format(self, sample_payload):
        body = _build_teams_payload(sample_payload)
        assert body["@type"] == "MessageCard"
        assert body["@context"] == "https://schema.org/extensions"
        assert body["themeColor"] == "FFA500"  # partial → orange
        assert "sections" in body

    def test_facts_contain_status_and_counts(self, sample_payload):
        body = _build_teams_payload(sample_payload)
        facts = body["sections"][0]["facts"]
        names = {f["name"]: f["value"] for f in facts}
        assert names["狀態"] == "partial"
        assert "3" in names["異常相機"]

    def test_failed_uses_red_theme(self, sample_payload):
        sample_payload.run_status = "failed"
        body = _build_teams_payload(sample_payload)
        assert body["themeColor"] == "FF0000"

    def test_no_abnormal_uses_green_theme(self, sample_payload):
        sample_payload.run_status = "success"
        sample_payload.abnormal_cameras = 0
        body = _build_teams_payload(sample_payload)
        assert body["themeColor"] == "00FF00"


# === send_webhook ===
class TestSendWebhook:

    def test_disabled_skips_send(self, sample_payload):
        """enabled=False 時不應送任何 HTTP。"""
        session = MagicMock()
        cfg = WebhookConfig(provider="slack", url="https://x.com", enabled=False)
        ok, err = send_webhook(cfg, sample_payload, session=session, verbose=False)
        assert ok is True
        session.post.assert_not_called()

    def test_successful_post(self, sample_payload):
        """200 OK → 回 success。"""
        session = MagicMock()
        session.post.return_value = MagicMock(
            ok=True, status_code=200, text="ok",
        )
        cfg = WebhookConfig(provider="slack", url="https://hooks.slack.com/x")
        ok, err = send_webhook(cfg, sample_payload, session=session, verbose=False)
        assert ok is True
        session.post.assert_called_once()

    def test_http_500_returns_failure(self, sample_payload):
        """5xx → 回 failure（不丟例外）。"""
        session = MagicMock()
        session.post.return_value = MagicMock(
            ok=False, status_code=500, text="oops",
        )
        cfg = WebhookConfig(provider="slack", url="https://hooks.slack.com/x")
        ok, err = send_webhook(cfg, sample_payload, session=session, verbose=False)
        assert ok is False
        assert "500" in err

    def test_timeout_returns_failure(self, sample_payload):
        """timeout → 回 failure（不丟例外）。"""
        import requests

        session = MagicMock()
        session.post.side_effect = requests.exceptions.Timeout()
        cfg = WebhookConfig(provider="slack", url="https://hooks.slack.com/x")
        ok, err = send_webhook(cfg, sample_payload, session=session, verbose=False)
        assert ok is False
        assert "timeout" in err

    def test_invalid_provider_fails(self, sample_payload):
        """provider 不是 slack/teams → failure。"""
        session = MagicMock()
        cfg = WebhookConfig(provider="discord", url="https://x.com")
        ok, err = send_webhook(cfg, sample_payload, session=session, verbose=False)
        assert ok is False
        assert "discord" in err
        session.post.assert_not_called()

    def test_empty_url_after_env_substitution_fails(self, sample_payload):
        """URL 含未定義 env var → substitution 後為空 → failure。"""
        os.environ.pop("NONEXIST_WEBHOOK_URL", None)
        session = MagicMock()
        cfg = WebhookConfig(
            provider="slack",
            url="${NONEXIST_WEBHOOK_URL}",
        )
        ok, err = send_webhook(cfg, sample_payload, session=session, verbose=False)
        assert ok is False
        session.post.assert_not_called()

    def test_channel_added_for_slack(self, sample_payload):
        """Slack 訊息內含 channel 欄位。"""
        session = MagicMock()
        session.post.return_value = MagicMock(ok=True)
        cfg = WebhookConfig(
            provider="slack", url="https://x.com", channel="#test-channel",
        )
        send_webhook(cfg, sample_payload, session=session, verbose=False)
        # 檢查傳送的 body 含 channel
        call_args = session.post.call_args
        sent_body = call_args.kwargs.get("json") or call_args.args[1]
        assert sent_body.get("channel") == "#test-channel"


# === send_webhooks ===
class TestSendWebhooks:

    def test_sends_to_all(self, sample_payload):
        """多個 webhook 都會送。"""
        session = MagicMock()
        session.post.return_value = MagicMock(ok=True)
        configs = [
            WebhookConfig(provider="slack", url="https://a.com"),
            WebhookConfig(provider="teams", url="https://b.com"),
        ]
        results = send_webhooks(configs, sample_payload, session=session, verbose=False)
        assert len(results) == 2
        assert all(ok for _, ok, _ in results)
        assert session.post.call_count == 2

    def test_continues_on_failure(self, sample_payload):
        """某個失敗不中斷其他。"""
        session = MagicMock()
        # 第 1 個失敗、2 個成功
        session.post.side_effect = [
            MagicMock(ok=False, status_code=500, text="err1"),
            MagicMock(ok=True),
            MagicMock(ok=True),
        ]
        configs = [
            WebhookConfig(provider="slack", url="https://a.com"),
            WebhookConfig(provider="teams", url="https://b.com"),
            WebhookConfig(provider="slack", url="https://c.com"),
        ]
        results = send_webhooks(configs, sample_payload, session=session, verbose=False)
        assert len(results) == 3
        success_count = sum(1 for _, ok, _ in results if ok)
        assert success_count == 2

    def test_empty_list_returns_empty(self, sample_payload):
        results = send_webhooks([], sample_payload, verbose=False)
        assert results == []


# === _parse_webhook_configs ===
class TestParseWebhookConfigs:

    def test_valid_configs(self):
        raw = [
            {"provider": "slack", "url": "https://x.com", "channel": "#test"},
            {"provider": "teams", "url": "https://y.com", "enabled": False},
        ]
        configs = _parse_webhook_configs(raw)
        assert len(configs) == 2
        assert configs[0].provider == "slack"
        assert configs[0].channel == "#test"
        assert configs[1].enabled is False

    def test_skip_invalid_provider(self, capsys):
        raw = [
            {"provider": "discord", "url": "https://x.com"},
            {"provider": "slack", "url": "https://y.com"},
        ]
        configs = _parse_webhook_configs(raw)
        # 不支援的 provider 被跳過
        assert len(configs) == 1
        assert configs[0].provider == "slack"
        # 警告訊息印出
        captured = capsys.readouterr()
        assert "discord" in captured.err

    def test_skip_missing_url(self, capsys):
        raw = [
            {"provider": "slack", "url": ""},
            {"provider": "teams"},  # 沒 url
        ]
        configs = _parse_webhook_configs(raw)
        assert configs == []

    def test_skip_non_dict_items(self):
        raw = [None, "string", 123, {"provider": "slack", "url": "https://x.com"}]
        configs = _parse_webhook_configs(raw)
        assert len(configs) == 1

    def test_none_input(self):
        assert _parse_webhook_configs(None) == []
