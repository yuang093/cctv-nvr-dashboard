"""
tests/integration/conftest.py
=============================
整合測試共用 fixtures：
    - mock_nvr_server    : MockAvigilonServer（單台，會自動 stop）
    - multi_nvr_servers  : 多台 mock NVR，含成功/異常/login-fail 三種情境
    - integration_db     : tempfile SQLite DB（worker + web 共用）
    - integration_config : 對應多 NVR 的設定 dict
    - integration_credentials : 完整 credentials dict
    - flush_urllib3_warnings : pytest caplog 過濾 InsecureRequestWarning
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 整合測試也能 import 專案根目錄模組
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.sqlite_writer import SqliteWriter  # noqa: E402
from tests.integration.mock_acc import (  # noqa: E402
    MockAvigilonServer,
    make_abnormal_nvr,
    make_login_fail_nvr,
    make_normal_nvr,
)


@pytest.fixture
def flush_urllib3_warnings():
    """
    整合測試因 mock server 自簽憑證會引發 InsecureRequestWarning，
    pytest.ini 已過濾。但若測試中斷在更早的階段，仍 caplog 一次避免干擾輸出。
    """
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    yield


# === 單台 NVR ===
@pytest.fixture
def mock_nvr_server(flush_urllib3_warnings):
    """單台 mock NVR：預設 2 異常 + 1 正常相機 + 2 異常事件。"""
    server = MockAvigilonServer(make_abnormal_nvr())
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_nvr_normal(flush_urllib3_warnings):
    """單台全正常 NVR（零事件）。"""
    server = MockAvigilonServer(make_normal_nvr(camera_count=3))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_nvr_login_fail(flush_urllib3_warnings):
    """login 故意回 403（測試 batch_scan 吞失敗）。"""
    server = MockAvigilonServer(make_login_fail_nvr())
    server.start()
    yield server
    server.stop()


# === 多台 NVR（給 batch_scan）===
@pytest.fixture
def multi_nvr_servers(flush_urllib3_warnings):
    """
    3 台 mock NVR：
        0: 全正常（3 CONNECTED + 0 events）
        1: 異常（2 異常 + 1 正常 + 2 異常事件）— 預設 fixture
        2: login 失敗（403）
    """
    servers = [
        MockAvigilonServer(make_normal_nvr(camera_count=3)),
        MockAvigilonServer(make_abnormal_nvr()),
        MockAvigilonServer(make_login_fail_nvr()),
    ]
    for s in servers:
        s.start()
    yield servers
    for s in servers:
        s.stop()


@pytest.fixture
def integration_config(multi_nvr_servers):
    """把 multi_nvr_servers 對應到 batch_scan 用的 config（host/port 動態注入）。"""
    nvr_objs = [
        {
            "id": f"mock-{i}",
            "name": f"MockNVR-{i}",
            "host": s.host,
            "port": s.port,
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
            "enabled": True,
        }
        for i, s in enumerate(multi_nvr_servers)
    ]
    return {
        "scan_settings": {"db_path": ":memory:", "timeout_seconds": 5},
        "nvr_servers": nvr_objs,
    }


@pytest.fixture
def integration_credentials():
    """整合測試用的 credentials dict。"""
    return {
        "user_nonce": "test-nonce-int",
        "user_key": "test-key-int",
        "integration_id": "",
        "username_override": None,
        "password_override": None,
    }


# === DB ===
@pytest.fixture
def integration_db(tmp_path):
    """檔案式 SQLite DB（worker + web 跨連線讀寫用）。"""
    db_path = str(tmp_path / "integration.db")
    writer = SqliteWriter(db_path)
    yield db_path, writer
    import gc

    gc.collect()


@pytest.fixture
def integration_writer(tmp_path):
    """單一 SqliteWriter（給單 NVR 測試，不需要跨連線）。"""
    db_path = str(tmp_path / "integration_single.db")
    yield SqliteWriter(db_path)


# === Warnings filter ===
@pytest.fixture(autouse=True)
def _disable_insecure_warning_for_integration(request):
    """
    整合測試預設啟動 urllib3 warning 過濾（自簽 SSL）。
    pytest.ini 已設 filterwarnings，但某些工具（如 pytest -W error）會覆蓋。
    """
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    yield
