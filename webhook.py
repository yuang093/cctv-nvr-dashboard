"""
webhook.py
==========
Webhook 推播模組：在 batch_scan 偵測到異常時，自動 POST 到 Slack / Teams。

設計目標：
    - 不中斷 worker：webhook 失敗僅記 log，不丟例外
    - 環境變數注入 URL：避免明碼寫在 nvr_config.json
    - 多 webhook 並行：同一份掃描結果可送到多個 channel
    - 條件觸發：只在 abnormal_cameras > 0 時送
    - 可關閉：nvr_config.json 內 webhooks 段為空就跳過

支援 provider：
    - "slack"：Slack Incoming Webhooks（https://api.slack.com/messaging/webhooks）
    - "teams"：MS Teams Office 365 Connector（MessageCard 格式）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Literal

import requests


# === 常數 ===
WEBHOOK_TIMEOUT = 10  # HTTP 逾時秒數
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@dataclass
class WebhookConfig:
    """單個 webhook 設定（從 nvr_config.json 讀取）。"""

    provider: Literal["slack", "teams"]
    url: str
    channel: str | None = None  # 顯示用，Slack 才有實際效果
    enabled: bool = True


@dataclass
class WebhookPayload:
    """要送的訊息（batch_scan 結束時產出）。"""

    run_status: str  # 'success' / 'partial' / 'failed'
    total_nvrs: int
    ok_nvrs: int
    failed_nvrs: int
    total_cameras: int
    abnormal_cameras: int
    started_at: str
    finished_at: str
    per_nvr_results: list[dict] = field(default_factory=list)
    # 從 per_nvr_results 抽出前 5 大異常供訊息顯示
    top_anomaly_limit: int = 5


# === URL 環境變數替換 ===
def substitute_env_vars(url: str) -> str:
    """
    替換 URL 內的 ${VAR} 為實際環境變數值。

    範例：'${NVR_WEBHOOK_SLACK_URL}' → 'https://hooks.slack.com/...'
    若環境變數未設定，回空字串（呼叫端判斷跳過）。

    Args:
        url: 含 ${ENV_VAR} 佔位符的字串。

    Returns:
        替換後的字串（無未解析的佔位符）。
    """
    def _replace(match):
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    return ENV_VAR_PATTERN.sub(_replace, url)


# === 訊息格式產生 ===
def _build_slack_payload(payload: WebhookPayload) -> dict:
    """產生 Slack Incoming Webhook 接受的 JSON。"""
    # 計算 duration
    try:
        from datetime import datetime
        start = datetime.fromisoformat(payload.started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload.finished_at.replace("Z", "+00:00"))
        duration_sec = (end - start).total_seconds()
        duration_str = f"{duration_sec:.1f}s"
    except Exception:
        duration_str = "?"

    # 異常顏色（依 status）
    emoji = {
        "success": "✅",
        "partial": "⚠️",
        "failed": "🚨",
    }.get(payload.run_status, "ℹ️")

    # 抽出前 5 大異常
    top_anomalies: list[str] = []
    for nvr_result in payload.per_nvr_results:
        result = nvr_result.get("result", nvr_result)
        for ev in result.get("events", [])[:payload.top_anomaly_limit]:
            dev_id = ev.get("deviceId", "?")
            topics = ev.get("eventTopics") or [ev.get("eventTopic", "?")]
            topic_str = ", ".join(str(t) for t in topics[:3])
            top_anomalies.append(f"  • `{dev_id}`: {topic_str}")
            if len(top_anomalies) >= payload.top_anomaly_limit:
                break
        if len(top_anomalies) >= payload.top_anomaly_limit:
            break

    anomaly_block = "\n".join(top_anomalies) if top_anomalies else "  （無）"

    text = (
        f"{emoji} *NVR 掃描：{payload.run_status}*\n"
        f"*異常相機*：{payload.abnormal_cameras} / {payload.total_cameras}\n"
        f"*NVR 成功/失敗*：{payload.ok_nvrs} / {payload.failed_nvrs}（總 {payload.total_nvrs}）\n"
        f"*耗時*：{duration_str}\n"
        f"\n*前 {payload.top_anomaly_limit} 大異常*：\n{anomaly_block}"
    )

    message: dict = {
        "text": f"NVR 掃描異常：{payload.abnormal_cameras} 台異常 / {payload.total_cameras} 台總計",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ],
    }
    return message


def _build_teams_payload(payload: WebhookPayload) -> dict:
    """產生 MS Teams MessageCard JSON。"""
    # 主題色（依 abnormal 程度）
    if payload.abnormal_cameras == 0:
        theme_color = "00FF00"  # 綠
    elif payload.run_status == "failed":
        theme_color = "FF0000"  # 紅
    else:
        theme_color = "FFA500"  # 橘

    # 計算 duration
    try:
        from datetime import datetime
        start = datetime.fromisoformat(payload.started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload.finished_at.replace("Z", "+00:00"))
        duration_sec = (end - start).total_seconds()
        duration_str = f"{duration_sec:.1f}s"
    except Exception:
        duration_str = "?"

    facts = [
        {"name": "狀態", "value": payload.run_status},
        {"name": "異常相機", "value": f"{payload.abnormal_cameras} / {payload.total_cameras}"},
        {"name": "NVR 成功/失敗", "value": f"{payload.ok_nvrs} / {payload.failed_nvrs}（總 {payload.total_nvrs}）"},
        {"name": "耗時", "value": duration_str},
    ]

    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": f"NVR 掃描 {payload.run_status} — {payload.abnormal_cameras} 異常",
        "themeColor": theme_color,
        "title": f"NVR 掃描：{payload.run_status}",
        "sections": [
            {
                "activityTitle": "掃描結果",
                "facts": facts,
            }
        ],
    }


# === Webhook 送出 ===
def _post_json(
    url: str,
    body: dict,
    *,
    session: requests.Session | None = None,
    timeout: int = WEBHOOK_TIMEOUT,
) -> tuple[bool, str]:
    """POST 一份 JSON 到指定 URL。回傳 (success, error_message)。"""
    sess = session or requests.Session()
    try:
        resp = sess.post(
            url,
            json=body,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        if resp.ok:
            return True, ""
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.exceptions.Timeout:
        return False, f"timeout after {timeout}s"
    except requests.exceptions.RequestException as exc:
        return False, f"RequestException: {exc}"


def send_webhook(
    config: WebhookConfig,
    payload: WebhookPayload,
    *,
    session: requests.Session | None = None,
    timeout: int = WEBHOOK_TIMEOUT,
    verbose: bool = True,
) -> tuple[bool, str]:
    """
    送出單個 webhook。

    Args:
        config: WebhookConfig（含 provider / url / channel）。
        payload: WebhookPayload。
        session: 可注入的 requests.Session（測試用）。
        timeout: HTTP 逾時秒數。
        verbose: True 時印 log。

    Returns:
        (success, error_message)：失敗時 error_message 為人類可讀字串。
    """
    if not config.enabled:
        return True, "disabled"

    # 環境變數替換
    url = substitute_env_vars(config.url)
    if not url:
        msg = f"webhook URL 未設定（{config.provider}）：{config.url}"
        if verbose:
            print(f"[WARN] {msg}", file=sys.stderr)
        return False, msg

    # 產 payload
    if config.provider == "slack":
        body = _build_slack_payload(payload)
        if config.channel:
            body["channel"] = config.channel
    elif config.provider == "teams":
        body = _build_teams_payload(payload)
    else:
        msg = f"不支援的 webhook provider：{config.provider}"
        if verbose:
            print(f"[WARN] {msg}", file=sys.stderr)
        return False, msg

    # 送
    ok, err = _post_json(url, body, session=session, timeout=timeout)
    if verbose:
        if ok:
            print(f"[INFO] webhook 送出成功：{config.provider} → ...{url[-30:]}")
        else:
            print(f"[WARN] webhook 送出失敗：{config.provider} — {err}", file=sys.stderr)
    return ok, err


def send_webhooks(
    webhooks: list[WebhookConfig],
    payload: WebhookPayload,
    *,
    session: requests.Session | None = None,
    timeout: int = WEBHOOK_TIMEOUT,
    verbose: bool = True,
) -> list[tuple[WebhookConfig, bool, str]]:
    """
    對每個 webhook 依序送出（不並行，避免 race 與單一失敗放大）。

    全部失敗 → 仍回傳每個結果；不丟例外（避免 worker 中斷）。

    Returns:
        [(config, success, error_message), ...]，順序對應 webhooks 輸入。
    """
    results = []
    for cfg in webhooks:
        ok, err = send_webhook(
            cfg, payload, session=session, timeout=timeout, verbose=verbose,
        )
        results.append((cfg, ok, err))
    return results


# === CLI 測試入口 ===
def _cli() -> int:
    """獨立測試 webhook 模組：python -m webhook。"""
    import sys

    print("webhook module self-check:")
    print(f"  substitute_env_vars: {substitute_env_vars('${NONEXIST}')} (應為空)")
    sample_payload = WebhookPayload(
        run_status="partial",
        total_nvrs=3, ok_nvrs=2, failed_nvrs=1,
        total_cameras=8, abnormal_cameras=3,
        started_at="2026-06-30T10:00:00Z",
        finished_at="2026-06-30T10:00:05Z",
        per_nvr_results=[
            {"result": {"events": [
                {"deviceId": "cam-001",
                 "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"]}
            ]}}
        ],
    )
    print(f"  slack payload: {json.dumps(_build_slack_payload(sample_payload), ensure_ascii=False, indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
