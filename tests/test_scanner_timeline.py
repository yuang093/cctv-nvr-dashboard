"""
tests/test_scanner_timeline.py
===============================
AvigilonScanner.get_timeline() 單元測試。

/timeline 端點呼叫，目標單台 cam，伺服器回傳 24h 視窗內的 record 陣列。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nvr_scanner import AvigilonScanner


def _wrap(result):
    """與其他 endpoint 一致的 ACC 回應包裝。"""
    return MagicMock(
        status_code=200, ok=True,
        json=lambda: {"status": "success", "result": result},
        text="...",
    )


def test_get_timeline_uses_correct_endpoint(sample_nvr, mock_session, make_login_response):
    """get_timeline 應呼叫 /mt/api/rest/v1/timeline。"""
    mock_session.request.side_effect = [
        make_login_response("tok"),
        _wrap({"servers": [{"id": "acc7-id"}]}),  # /server/ids
        _wrap({"timelines": []}),  # /timeline 結果
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    s.login()
    s.get_timeline("cam-1", from_iso="2026-07-28T00:00:00Z", to_iso="2026-07-29T00:00:00Z")
    # 第三次呼叫是 /timeline（args = (method, url, ...)；url 在 args[1]）
    third = mock_session.request.call_args_list[2]
    url = third.args[1] if len(third.args) > 1 else third.kwargs.get("url", "")
    assert "/timeline" in url


def test_get_timeline_returns_parsed_data(sample_nvr, mock_session, make_login_response):
    """get_timeline 回傳的物件應為 unwrap 過的結果（dict）。"""
    mock_session.request.side_effect = [
        make_login_response("tok"),
        _wrap({"servers": [{"id": "acc7-id"}]}),
        _wrap({"timelines": [{"cameraId": "cam-1", "record": [
            {"start": "2026-07-28T00:00:00Z", "end": "2026-07-28T01:00:00Z"},
        ]}]}),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    s.login()
    data = s.get_timeline("cam-1", from_iso="2026-07-28T00:00:00Z", to_iso="2026-07-29T00:00:00Z")
    assert "timelines" in data
    assert data["timelines"][0]["cameraId"] == "cam-1"


def test_get_timeline_uses_server_id_from_server_ids_endpoint(sample_nvr, mock_session, make_login_response):
    """get_timeline 走 get_server_ids()（不再用硬編 ID）。

    因 _parse_server_id 已經修好 ACC 7+ 結構，正常路徑：
    login → server/ids → timeline。
    """
    mock_session.request.side_effect = [
        make_login_response("tok"),
        _wrap({"servers": [{"id": "acc7-id", "name": "X"}]}),  # ACC 7+ 結構
        _wrap({"timelines": []}),
    ]
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    s.login()
    s.get_timeline("cam-1", from_iso="2026-07-28T00:00:00Z", to_iso="2026-07-29T00:00:00Z")
    call = mock_session.request.call_args_list[2]
    params = call.kwargs.get("params", {})
    assert params["serverId"] == "acc7-id"  # 從 /server/ids 解出
    assert params["cameraIds"] == "cam-1"
    assert params["from"] == "2026-07-28T00:00:00Z"
    assert params["to"] == "2026-07-29T00:00:00Z"


def test_get_timeline_requires_login(sample_nvr, mock_session):
    """未登入呼叫 get_timeline 應拋 ScannerError。"""
    s = AvigilonScanner(
        sample_nvr, user_nonce="n", user_key="k", session=mock_session,
    )
    with pytest.raises(Exception):
        s.get_timeline("cam-1", from_iso="2026-07-28T00:00:00Z", to_iso="2026-07-29T00:00:00Z")
