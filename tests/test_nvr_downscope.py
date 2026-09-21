"""Week 5 #016：NVR 端 api_reader 權限自我驗證測試（Task 9）。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nvr_auth_check import check_nvr_permissions


def _make_mock_session(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """產生 mock requests.Session。"""
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {"data": {}}
    mock_resp.raise_for_status.return_value = None
    mock_session.get.return_value = mock_resp
    return mock_session


def test_check_passes_when_user_has_readonly_role() -> None:
    """NVR 帳號具備 api_reader 角色 → PASS。"""
    mock_session = _make_mock_session(
        json_data={"data": {"roles": [{"name": "api_reader"}]}}
    )
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="fake-token")
    assert ok is True
    assert "api_reader" in msg


def test_check_passes_when_user_has_viewer_role() -> None:
    """NVR 帳號具備 Viewer 角色 → PASS（兼容 ACC 不同版本）。"""
    mock_session = _make_mock_session(
        json_data={"data": {"roles": [{"name": "Viewer"}]}}
    )
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is True
    assert "Viewer" in msg


def test_check_fails_when_user_lacks_readonly_role() -> None:
    """NVR 帳號只有 admin → FAIL。"""
    mock_session = _make_mock_session(
        json_data={"data": {"roles": [{"name": "admin"}]}}
    )
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is False
    assert "api_reader" in msg


def test_check_fails_when_user_has_no_roles() -> None:
    """NVR 帳號沒有任何 role → FAIL。"""
    mock_session = _make_mock_session(json_data={"data": {"roles": []}})
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is False


def test_check_handles_network_error_gracefully() -> None:
    """網路錯誤 → 回傳 (False, error) 而非拋例外。"""
    import requests

    mock_session = MagicMock()
    mock_session.get.side_effect = requests.ConnectionError("network down")
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is False
    assert "FAIL" in msg


def test_check_handles_http_error() -> None:
    """HTTP 4xx/5xx → 回傳 (False, error)。"""
    import requests

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    mock_session.get.return_value = mock_resp
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is False
    assert "FAIL" in msg


def test_check_handles_missing_roles_field() -> None:
    """API 回傳沒有 roles 欄位 → 視為無 role → FAIL。"""
    mock_session = _make_mock_session(json_data={"data": {"username": "admin"}})
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is False


def test_check_handles_empty_data() -> None:
    """API 回傳空 data → 視為無 role → FAIL。"""
    mock_session = _make_mock_session(json_data={"data": {}})
    with patch("nvr_auth_check.requests.Session", return_value=mock_session):
        ok, msg = check_nvr_permissions(host="1.2.3.4", session_token="t")
    assert ok is False
