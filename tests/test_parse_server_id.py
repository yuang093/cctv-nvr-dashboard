"""
tests/test_parse_server_id.py
==============================
驗證 `_parse_server_id` 容錯處理（ACC 7+ 結構）。

歷史 bug：ACC 7+ /server/ids 回傳結果包在 `result.servers` 陣列內，
而舊版只展開 `result` 不展開 `servers` → 拿到字串 "None"。

修正：
- 移除 `result` 已被 unwrap_response 解開，所以 `_parse_server_id` 應看到
  `{"servers": [{"id": "..."}]}` 結構。
- 支援 `servers` 陣列 wrapper。
"""
from __future__ import annotations

import pytest

from nvr_scanner import AvigilonScanner, ApiResponseError


# === ACC 7+ 結構：result.servers 包 list ===
def test_parse_server_id_handles_servers_wrapper():
    """ACC 7+ 結構：解開後是 `{"servers": [{"id": "..."}]}`。"""
    data = {"servers": [{"id": "kw9bbJXtQOKu88ykHSZhfg", "name": "WIN-X"}]}
    assert AvigilonScanner._parse_server_id(data) == "kw9bbJXtQOKu88ykHSZhfg"


def test_parse_server_id_handles_servers_wrapper_acc_6():
    """ACC 6.x 結構：解開後是 `{"servers": [{"serverId": "..."}]}`。"""
    data = {"servers": [{"serverId": "old-id", "name": "X"}]}
    assert AvigilonScanner._parse_server_id(data) == "old-id"


def test_parse_server_id_handles_serverId_field_at_root():
    """舊版單機 NVR：解開後是 `{"serverId": "..."}` (沒 servers wrapper)。"""
    data = {"serverId": "root-id"}
    assert AvigilonScanner._parse_server_id(data) == "root-id"


def test_parse_server_id_handles_id_field_at_root():
    """ACC 7+ 單機 (理論上)：解開後是 `{"id": "..."}`。"""
    data = {"id": "root-id-2"}
    assert AvigilonScanner._parse_server_id(data) == "root-id-2"


def test_parse_server_id_handles_direct_list():
    """舊版：解開後直接是 list `[{...}]`。"""
    data = [{"id": "list-id"}]
    assert AvigilonScanner._parse_server_id(data) == "list-id"


def test_parse_server_id_handles_bare_string():
    """極端：直接是字串。"""
    assert AvigilonScanner._parse_server_id("just-id") == "just-id"


def test_parse_server_id_raises_on_empty_servers():
    """servers 陣列空 → 拋 ApiResponseError。"""
    with pytest.raises(ApiResponseError, match="servers"):
        AvigilonScanner._parse_server_id({"servers": []})


def test_parse_server_id_raises_on_empty_list():
    """直接 list 空 → 拋 ApiResponseError。"""
    with pytest.raises(ApiResponseError, match="空"):
        AvigilonScanner._parse_server_id([])


def test_parse_server_id_raises_on_unparseable():
    """無法解析的結構 → 拋 ApiResponseError。"""
    with pytest.raises(ApiResponseError, match="無法解析"):
        AvigilonScanner._parse_server_id(123)
