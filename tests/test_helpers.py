"""
tests/test_helpers.py
=====================
工具函式測試：
    - compute_authorization_token
    - load_env_file
    - unwrap_response
    - _extract_list
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nvr_scanner import (
    AuthError,
    _extract_list,
    compute_authorization_token,
    load_env_file,
    unwrap_response,
)


# === 1. compute_authorization_token 基本格式 ===
def test_compute_authorization_token_format():
    tok = compute_authorization_token(
        user_nonce="abc", user_key="xyz", timestamp=1700000000
    )
    parts = tok.split(":")
    assert len(parts) == 4
    assert parts[0] == "abc"
    assert parts[1] == "1700000000"
    assert len(parts[2]) == 64  # SHA-256 hex
    assert parts[3] == ""  # integrationId 預設空


def test_compute_authorization_token_with_integration_id():
    tok = compute_authorization_token(
        user_nonce="n",
        user_key="k",
        timestamp=1700000000,
        integration_id="integ-1",
    )
    parts = tok.split(":")
    assert parts[3] == "integ-1"


def test_compute_authorization_token_default_timestamp():
    """timestamp=None 時自動取當下時間。"""
    before = int(time.time())
    tok = compute_authorization_token(user_nonce="n", user_key="k")
    after = int(time.time())
    parts = tok.split(":")
    ts = int(parts[1])
    assert before <= ts <= after


def test_compute_authorization_token_deterministic():
    """同樣 input 應產生同樣 output。"""
    a = compute_authorization_token("n", "k", timestamp=1234567890)
    b = compute_authorization_token("n", "k", timestamp=1234567890)
    assert a == b


def test_compute_authorization_token_sha256_correctness():
    """hex = SHA-256(str(timestamp) + userKey).hexdigest()。"""
    tok = compute_authorization_token("n", "k", timestamp=1234567890)
    parts = tok.split(":")
    expected = __import__("hashlib").sha256(b"1234567890k").hexdigest()
    assert parts[2] == expected


# === 2. compute_authorization_token 缺值拋 AuthError ===
def test_compute_token_raises_on_empty_nonce():
    with pytest.raises(AuthError, match="必填"):
        compute_authorization_token("", "k", timestamp=1)


def test_compute_token_raises_on_empty_key():
    with pytest.raises(AuthError, match="必填"):
        compute_authorization_token("n", "", timestamp=1)


# === 3. load_env_file 基本 ===
def test_load_env_file_basic(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text(
        "FOO=bar\n"
        "BAZ=qux\n"
        "# 這是註解\n"
        "\n"  # 空行
        "EMPTY=\n",
        encoding="utf-8",
    )
    import os

    for k in ("FOO", "BAZ", "EMPTY"):
        os.environ.pop(k, None)
    n = load_env_file(p)
    assert n == 3
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"
    assert os.environ["EMPTY"] == ""
    # 清理
    for k in ("FOO", "BAZ", "EMPTY"):
        os.environ.pop(k, None)


# === 4. load_env_file 不覆蓋既有環境變數 ===
def test_load_env_file_does_not_override_existing(tmp_path: Path):
    import os

    p = tmp_path / ".env"
    p.write_text("EXISTING_VAR=from_file\n", encoding="utf-8")
    os.environ["EXISTING_VAR"] = "from_shell"
    n = load_env_file(p)
    assert n == 0  # 沒載入任何（因為已存在）
    assert os.environ["EXISTING_VAR"] == "from_shell"
    del os.environ["EXISTING_VAR"]


# === 5. load_env_file 引號剝除 ===
def test_load_env_file_unquotes(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text(
        'DOUBLE="double quoted"\n' "SINGLE='single quoted'\n" "PLAIN=no quotes\n",
        encoding="utf-8",
    )
    import os

    n = load_env_file(p)
    assert n == 3
    assert os.environ["DOUBLE"] == "double quoted"
    assert os.environ["SINGLE"] == "single quoted"
    assert os.environ["PLAIN"] == "no quotes"
    for k in ("DOUBLE", "SINGLE", "PLAIN"):
        os.environ.pop(k, None)


# === 6. load_env_file 檔案不存在回 0 ===
def test_load_env_file_missing_returns_zero(tmp_path: Path):
    p = tmp_path / "no_such_file.env"
    assert load_env_file(p) == 0


# === 7. load_env_file 跳過無等號行 ===
def test_load_env_file_skips_no_equals(tmp_path: Path):
    p = tmp_path / ".env"
    p.write_text(
        "VALID=1\n" "INVALID_NO_EQUALS\n" "ALSO_VALID=2\n",
        encoding="utf-8",
    )
    import os

    n = load_env_file(p)
    assert n == 2
    assert "INVALID_NO_EQUALS" not in os.environ
    for k in ("VALID", "ALSO_VALID"):
        os.environ.pop(k, None)


# === 8. unwrap_response 標準包裝 ===
def test_unwrap_response_standard():
    data = {"status": "success", "result": {"x": 1}}
    assert unwrap_response(data) == {"x": 1}


def test_unwrap_response_uses_data_key():
    data = {"status": "success", "data": [1, 2, 3]}
    assert unwrap_response(data) == [1, 2, 3]


def test_unwrap_response_uses_payload_key():
    data = {"status": "success", "payload": "hello"}
    assert unwrap_response(data) == "hello"


def test_unwrap_response_passthrough_non_success():
    """status != 'success' 不解開。"""
    data = {"status": "error", "code": 500}
    assert unwrap_response(data) == data


def test_unwrap_response_passthrough_non_dict():
    assert unwrap_response([1, 2, 3]) == [1, 2, 3]
    assert unwrap_response("plain string") == "plain string"
    assert unwrap_response(42) == 42


# === 9. _extract_list 各種形狀 ===
def test_extract_list_direct_list():
    assert _extract_list([1, 2, 3]) == [1, 2, 3]


def test_extract_list_named_wrapper():
    data = {"cameras": [{"id": "d1"}], "other": "x"}
    assert _extract_list(data, "cameras", "items") == [{"id": "d1"}]


def test_extract_list_singleton_dict_unwraps():
    """dict 有唯一 list value 時自動解開。"""
    data = {"items": [{"id": "d1"}]}
    assert _extract_list(data, "cameras", "items") == [{"id": "d1"}]


def test_extract_list_dict_of_objects():
    """dict-of-objects 形式：取所有 dict value。"""
    data = {
        "d1": {"id": "d1", "name": "cam1"},
        "d2": {"id": "d2", "name": "cam2"},
    }
    result = _extract_list(data, "cameras")
    assert len(result) == 2
    assert {"id": "d1", "name": "cam1"} in result
    assert {"id": "d2", "name": "cam2"} in result


def test_extract_list_empty_fallback():
    """完全無法提取回空 list。"""
    assert _extract_list("not a list", "cameras") == []
    assert _extract_list(None, "cameras") == []
    assert _extract_list({"key": "value"}, "cameras") == []


def test_extract_list_none_inner():
    """result 為 None 時 fallback。"""
    data = {"status": "success", "result": None}
    assert _extract_list(data, "cameras") == []
