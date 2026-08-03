"""
tests/test_scanner.py
======================
AvigilonScanner 單元測試。

策略：建構 AvigilonScanner 時注入 mock_session，
      設定 session.request.return_value / side_effect 模擬 API 回應。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nvr_scanner import (
    ABNORMAL_KEYWORDS,
    ApiResponseError,
    AuthError,
    AvigilonScanner,
    ConnectionError_,
)


# === 1. 建構子基本檢查 ===
def test_init_basic(sample_nvr, mock_session):
    s = AvigilonScanner(
        sample_nvr,
        user_nonce="n", user_key="k",
        session=mock_session,
    )
    assert s.base_url == "https://192.168.1.100:8443"
    assert s._session_token is None
    assert s._server_id is None
    assert s.session is mock_session


def test_init_custom_port(sample_nvr, mock_session):
    sample_nvr["port"] = 9443
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    assert s.base_url == "https://192.168.1.100:9443"


# === 2. login 成功 ===
def test_login_success(sample_nvr, mock_session, make_login_response):
    mock_session.request.return_value = make_login_response("token-xyz")
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    token = s.login()
    assert token == "token-xyz"
    assert s._session_token == "token-xyz"
    # 確認呼叫了正確的端點
    call = mock_session.request.call_args
    assert call.args[0] == "POST"  # method
    assert call.args[1].endswith("/mt/api/rest/v1/login")
    body = call.kwargs["json"]
    assert body["username"] == "admin"
    assert body["password"] == "secret"
    assert body["clientName"]  # 非空
    assert "authorizationToken" in body
    # authorizationToken 格式：nonce:ts:hex:integrationId
    parts = body["authorizationToken"].split(":")
    assert len(parts) == 4
    assert parts[0] == "n"
    assert len(parts[2]) == 64  # SHA-256 hex


# === 3. login 缺 session 欄位 → AuthError ===
def test_login_missing_session_raises(sample_nvr, mock_session):
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {"status": "success", "result": {"not_session": "x"}}
    resp.text = "..."
    mock_session.request.return_value = resp
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    with pytest.raises(AuthError, match="找不到 session token"):
        s.login()


# === 4. login 401/403 → AuthError ===
def test_login_403_raises_auth_error(sample_nvr, mock_session):
    resp = MagicMock()
    resp.status_code = 403
    resp.ok = False
    resp.text = "Forbidden"
    mock_session.request.return_value = resp
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    with pytest.raises(AuthError, match="授權失敗"):
        s.login()


# === 5. login timeout → ConnectionError_ ===
def test_login_timeout_raises_connection_error(sample_nvr, mock_session):
    import requests
    mock_session.request.side_effect = requests.exceptions.Timeout("read timeout")
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    with pytest.raises(ConnectionError_, match="連線逾時"):
        s.login()


# === 6. login SSL error → ConnectionError_ ===
def test_login_ssl_error_raises(sample_nvr, mock_session):
    import requests
    mock_session.request.side_effect = requests.exceptions.SSLError("bad cert")
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    with pytest.raises(ConnectionError_, match="SSL 錯誤"):
        s.login()


# === 7. get_cameras：解析 + 帶 pageSize ===
def test_get_cameras_uses_page_size(sample_nvr, mock_session, make_login_response, make_cameras_response):
    # 第一次 login、第二次 server/ids、第三次 cameras
    mock_session.request.side_effect = [
        make_login_response("tok"),
        MagicMock(
            status_code=200, ok=True,
            json=lambda: {"status": "success", "result": ["server-1"]},
            text="...",
        ),
        make_cameras_response([
            {"deviceId": "d1", "name": "cam1",
             "connectionStatus": {"state": "CONNECTED"}, "available": True},
        ]),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    s.login()  # 先取得 session token
    cams = s.get_cameras()
    assert "d1" in cams
    assert cams["d1"]["name"] == "cam1"
    assert cams["d1"]["connection_state"] == "CONNECTED"
    # 第三次呼叫（cameras 端點）帶 pageSize
    third_call = mock_session.request.call_args_list[2]
    assert third_call.kwargs["params"]["pageSize"] == 100


# === 8. get_active_events：回傳 raw events ===
def test_get_active_events(sample_nvr, mock_session, make_login_response, make_events_response):
    mock_session.request.side_effect = [
        make_login_response("tok"),
        MagicMock(
            status_code=200, ok=True,
            json=lambda: {"status": "success", "result": ["s1"]},
            text="...",
        ),
        make_events_response([
            {"eventId": "e1", "deviceId": "d1",
             "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"]},
        ]),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    s.login()
    events = s.get_active_events()
    assert len(events) == 1
    assert events[0]["eventId"] == "e1"
    # 第三次呼叫（events 端點）帶 queryType=ACTIVE
    third_call = mock_session.request.call_args_list[2]
    assert third_call.kwargs["params"]["queryType"] == "ACTIVE"


# === 9. _is_abnormal 關鍵字匹配（eventTopics）===
def test_is_abnormal_keyword_match():
    from nvr_scanner import AvigilonScanner
    # 匹配 DEVICE_VIDEO_SIGNAL_LOST
    assert AvigilonScanner._is_abnormal({
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
    })
    # 匹配 DEVICE_TAMPERING
    assert AvigilonScanner._is_abnormal({
        "eventTopics": ["DEVICE_TAMPERING"],
    })
    # 不匹配
    assert not AvigilonScanner._is_abnormal({
        "eventTopics": ["DEVICE_NORMAL_EVENT"],
    })
    # 單值 eventTopic
    assert AvigilonScanner._is_abnormal({
        "eventTopic": "DEVICE_COMMUNICATION_LOST",
    })


# === 10. _is_abnormal 處理 string 而非 list ===
def test_is_abnormal_handles_string_topics():
    from nvr_scanner import AvigilonScanner
    # eventTopics 為字串而非 list
    assert AvigilonScanner._is_abnormal({
        "eventTopics": "DEVICE_VIDEO_SIGNAL_LOST",
    })


# === 11. scan() 雙重異常檢查：events + connectionStatus.state ===
def test_scan_double_check_events_and_state(sample_nvr, mock_session, make_login_response, make_cameras_response, make_events_response):
    mock_session.request.side_effect = [
        make_login_response("tok"),
        MagicMock(
            status_code=200, ok=True,
            json=lambda: {"status": "success", "result": ["s1"]},
            text="...",
        ),
        # cameras: d1 CONNECTED, d2 LONG_FAILED
        make_cameras_response([
            {"deviceId": "d1", "name": "cam1",
             "connectionStatus": {"state": "CONNECTED"}, "available": True},
            {"deviceId": "d2", "name": "cam2",
             "connectionStatus": {"state": "LONG_FAILED"}, "available": False},
        ]),
        # events: d1 異常（VIDEO_LOSS）
        make_events_response([
            {"eventId": "e1", "deviceId": "d1",
             "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
             "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
             "occurred_at": "2026-06-23T00:00:00Z"},
        ]),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    result = s.scan()

    # 兩台相機：d1 來自 events、d2 來自 state 異常
    assert result["stats"]["total_cameras"] == 2
    assert result["stats"]["abnormal_cameras"] == 2
    abnormal_devices = {e["deviceId"] for e in result["events"]}
    assert abnormal_devices == {"d1", "d2"}
    # d2 是 state 異常（合成）
    d2_event = next(e for e in result["events"] if e["deviceId"] == "d2")
    assert d2_event["source"] == "camera_state"


# === 12. scan() 去重：event + state 同相機只列一次 ===
def test_scan_dedup_event_and_state_same_camera(sample_nvr, mock_session, make_login_response, make_cameras_response, make_events_response):
    mock_session.request.side_effect = [
        make_login_response("tok"),
        MagicMock(
            status_code=200, ok=True,
            json=lambda: {"status": "success", "result": ["s1"]},
            text="...",
        ),
        # cameras: d1 LONG_FAILED
        make_cameras_response([
            {"deviceId": "d1", "name": "cam1",
             "connectionStatus": {"state": "LONG_FAILED"}, "available": False},
        ]),
        # events: d1 也有 VIDEO_LOSS（雙重命中）
        make_events_response([
            {"eventId": "e1", "deviceId": "d1",
             "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
             "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
             "occurred_at": "2026-06-23T00:00:00Z"},
        ]),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    result = s.scan()
    # 1 台異常相機、1 筆 event（去重）
    assert result["stats"]["abnormal_cameras"] == 1
    assert len(result["events"]) == 1
    # 保留的是 event（不是合成 state 事件）
    assert result["events"][0]["eventTopic"] == "DEVICE_VIDEO_SIGNAL_LOST"


# === 13. scan() 無異常事件 ===
def test_scan_no_abnormal(sample_nvr, mock_session, make_login_response, make_cameras_response, make_events_response):
    mock_session.request.side_effect = [
        make_login_response("tok"),
        MagicMock(
            status_code=200, ok=True,
            json=lambda: {"status": "success", "result": ["s1"]},
            text="...",
        ),
        make_cameras_response([
            {"deviceId": "d1", "name": "cam1",
             "connectionStatus": {"state": "CONNECTED"}, "available": True},
        ]),
        make_events_response([]),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    result = s.scan()
    assert result["stats"]["total_cameras"] == 1
    assert result["stats"]["abnormal_cameras"] == 0
    assert result["events"] == []


# === 14. ABNORMAL_KEYWORDS 是 non-empty tuple ===
def test_abnormal_keywords_non_empty():
    assert len(ABNORMAL_KEYWORDS) > 0
    assert "DEVICE_VIDEO_SIGNAL_LOST" in ABNORMAL_KEYWORDS
