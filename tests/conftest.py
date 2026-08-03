"""
tests/conftest.py
==================
pytest 共用 fixtures。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 讓測試可 import 專案根目錄的模組
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.sqlite_writer import SqliteWriter  # noqa: E402


# === DB fixture ===
@pytest.fixture
def memory_db():
    """:memory: SQLite DB（測試結束自動 GC，連線隨 SqliteWriter 物件消滅）"""
    return SqliteWriter(":memory:")


@pytest.fixture
def tmp_db(tmp_path):
    """檔案式 SQLite DB（測 cross-instance 持久化）。"""
    db_path = str(tmp_path / "test.db")
    yield SqliteWriter(db_path)
    # 清理：gc 釋放檔案 lock（Windows）
    import gc
    gc.collect()


# === 設定 fixture ===
@pytest.fixture
def sample_nvr():
    """單台 NVR 設定 dict。"""
    return {
        "id": "test-nvr-1",
        "name": "測試 NVR",
        "host": "192.168.1.100",
        "port": 8443,
        "username": "admin",
        "password": "secret",
        "verify_ssl": False,
        "enabled": True,
    }


@pytest.fixture
def sample_credentials():
    """完整 credentials dict（含環境變數覆寫欄位）。"""
    return {
        "user_nonce": "test-nonce",
        "user_key": "test-key",
        "integration_id": "",
        "username_override": None,
        "password_override": None,
    }


@pytest.fixture
def sample_nvr_config(sample_nvr):
    """包成 batch_scan 用的 config 結構（含 scan_settings + 單台 NVR）。"""
    return {
        "scan_settings": {"db_path": ":memory:", "timeout_seconds": 5},
        "nvr_servers": [sample_nvr],
    }


@pytest.fixture
def three_nvrs_config():
    """3 台 NVR（1 成功 + 2 失敗用）。"""
    def _mk(nvr_id, host):
        return {
            "id": nvr_id,
            "name": f"NVR-{nvr_id}",
            "host": host,
            "port": 8443,
            "username": "u",
            "password": "p",
            "verify_ssl": False,
            "enabled": True,
        }
    return {
        "scan_settings": {"db_path": ":memory:", "timeout_seconds": 5},
        "nvr_servers": [
            _mk("A", "10.0.0.1"),
            _mk("B", "10.0.0.2"),
            _mk("C", "10.0.0.3"),
        ],
    }


# === Mock HTTP Session fixture ===
class MockResponse:
    """requests.Response 的極簡 mock（只支援 batch_scan / scanner 測試需要的方法）。"""

    def __init__(self, json_data=None, status_code=200, text=""):
        self._json = json_data
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text or (str(json_data) if json_data else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture
def mock_session():
    """requests.Session mock；測試可在 post/get 設定 return_value / side_effect。"""
    s = MagicMock()
    s.verify = False
    return s


@pytest.fixture
def make_login_response():
    """產生 ACC login 成功回應。"""
    def _make(token="session-abc-123"):
        return MockResponse({
            "status": "success",
            "result": {"session": token}
        })
    return _make


@pytest.fixture
def make_cameras_response():
    """產生 cameras 回應（包裝或裸 list 都接受）。"""
    def _make(cameras):
        return MockResponse({
            "status": "success",
            "result": {"cameras": cameras}
        })
    return _make


@pytest.fixture
def make_events_response():
    """產生 events/search 回應。"""
    def _make(events):
        return MockResponse({
            "status": "success",
            "result": {"events": events}
        })
    return _make
