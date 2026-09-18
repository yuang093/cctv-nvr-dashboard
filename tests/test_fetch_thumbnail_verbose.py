"""
tests/test_fetch_thumbnail_verbose.py
=======================================
2026-07-30：fetch_thumbnail_with_status（verbose 版）回傳 (bytes, error_msg)。

理由：原本 fetch_thumbnail 回傳 bytes | None，任何錯誤都吞掉。
verbose 版把錯誤訊息回傳，給 batch_scan 與未來 caller 診斷用。

不破壞既有 fetch_thumbnail 介面（保持回傳 bytes | None），新增方法並用：
  - 成功 → (jpeg_bytes, None)
  - 失敗 → (None, "HTTP 403"/"HTTP 404"/"non-JPEG ..."/"ConnectionError: ..."/"尚未登入...")
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nvr_scanner import AvigilonScanner


def _make_scanner(session_token: str | None = "tok-abc") -> AvigilonScanner:
    """建一個 AvigilonScanner（不實際連線）。"""
    s = AvigilonScanner.__new__(AvigilonScanner)
    s._session_token = session_token
    s.session = MagicMock()
    s.base_url = "https://10.0.0.1:8443"
    s.timeout = 5
    return s


# === 1. 未登入 ===
def test_no_session_returns_error():
    s = _make_scanner(session_token=None)
    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b is None
    assert err == "尚未登入（_session_token is None）"


# === 2. 成功回 JPEG ===
def test_success_returns_bytes_no_error():
    from PIL import Image
    from io import BytesIO

    img = Image.new("RGB", (10, 10), (128, 128, 128))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    s = _make_scanner()
    resp = MagicMock()
    resp.ok = True
    resp.content = jpeg_bytes
    resp.status_code = 200
    s.session.request.return_value = resp

    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b == jpeg_bytes
    assert err is None


# === 3. HTTP 403 (NVR 拒絕) ===
def test_http_403_returns_error():
    s = _make_scanner()
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 403
    resp.reason = "Forbidden"
    s.session.request.return_value = resp

    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b is None
    assert "HTTP 403" in err
    assert "Forbidden" in err


# === 4. HTTP 404 ===
def test_http_404_returns_error():
    s = _make_scanner()
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 404
    resp.reason = "Not Found"
    s.session.request.return_value = resp

    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b is None
    assert "HTTP 404" in err


# === 5. HTTP 200 但 0 bytes ===
def test_http_200_empty_returns_error():
    s = _make_scanner()
    resp = MagicMock()
    resp.ok = True
    resp.content = b""
    resp.status_code = 200
    s.session.request.return_value = resp

    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b is None
    assert "0 bytes" in err


# === 6. 非 JPEG 回應（HTML 錯誤頁） ===
def test_non_jpeg_response_returns_error():
    s = _make_scanner()
    resp = MagicMock()
    resp.ok = True
    resp.content = b"<html><body>Error</body></html>"
    resp.status_code = 200
    s.session.request.return_value = resp

    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b is None
    assert "非 JPEG" in err


# === 7. 連線例外 ===
def test_connection_error_returns_error():
    s = _make_scanner()
    s.session.request.side_effect = ConnectionError("refused")

    b, err = s.fetch_thumbnail_with_status("cam-1")
    assert b is None
    assert "ConnectionError" in err
    assert "refused" in err


# === 8. at_time 帶時間戳 ===
def test_at_time_param_in_request():
    from datetime import datetime, timezone

    s = _make_scanner()
    resp = MagicMock()
    resp.ok = True
    resp.content = b"\xff\xd8\xff\xe0" + b"fake-jpeg-bytes"  # magic + content
    resp.status_code = 200
    s.session.request.return_value = resp

    at = datetime(2026, 7, 30, 6, 0, 0, tzinfo=timezone.utc)
    s.fetch_thumbnail_with_status("cam-1", at_time=at)
    params = s.session.request.call_args.kwargs["params"]
    assert params["cameraId"] == "cam-1"
    assert params["format"] == "jpeg"
    assert params["t"] == "2026-07-30T06:00:00.000Z"
    assert "session" not in params or params.get("session") == "tok-abc"
