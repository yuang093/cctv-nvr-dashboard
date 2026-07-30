"""
tests/test_batch_scan.py
=========================
batch_scan 多 NVR 協調器測試。

策略：mock AvigilonScanner（patch batch_scan.AvigilonScanner），
      讓 .scan() 回傳 canned dict 或拋 canned 例外。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from batch_scan import batch_scan
from nvr_scanner import AuthError, ScannerError


def _ok_scan(nvr_id, total_cams=2, abnormal=1):
    """產生一筆成功的 scan 結果（含 connection_state 異常的相機）。"""
    return {
        "nvr_id": nvr_id,
        "nvr_name": f"NVR-{nvr_id}",
        "cameras": {
            "d1": {"name": "cam1",
                   "connection_state": "CONNECTED", "available": True},
            "d2": {"name": "cam2",
                   "connection_state": "LONG_FAILED", "available": False},
        },
        "events": [{
            "deviceId": "d2",
            "eventTopics": ["STATE_LONG_FAILED"],
            "eventTopic": "STATE_LONG_FAILED",
            "source": "camera_state",
        }],
        "stats": {"total_cameras": total_cams, "abnormal_cameras": abnormal},
    }


def _patch_scanner(side_effect_fn):
    """patch batch_scan.AvigilonScanner，讓 .scan() 走 side_effect_fn(nvr_config)。"""
    return patch(
        "batch_scan.AvigilonScanner",
        side_effect=lambda nv, **kw: MagicMock(
            scan=MagicMock(side_effect=lambda: side_effect_fn(nv["id"]))
        ),
    )


# === 1. partial：3 台中 1 成功 + 2 失敗 ===
def test_partial_status_with_one_success(three_nvrs_config, sample_credentials, memory_db):
    config = three_nvrs_config
    creds = sample_credentials

    def side(nvr_id):
        if nvr_id == "A":
            return _ok_scan("A")
        elif nvr_id == "B":
            raise ConnectionError_(f"無法連線 {nvr_id}")
        else:
            raise AuthError(f"HTTP 403 on {nvr_id}")

    with _patch_scanner(side):
        result = batch_scan(config, creds, memory_db, verbose=False)

    assert result["status"] == "partial"
    assert result["total_nvrs"] == 3
    assert result["ok_nvrs"] == 1
    assert result["failed_nvrs"] == 2
    assert result["total_cameras"] == 2
    assert result["abnormal_cameras"] == 1
    assert [f["nvr_id"] for f in result["failures"]] == ["B", "C"]


# === 2. success：全部 NVR 都成功 ===
def test_all_success_status(sample_nvr_config, sample_credentials, memory_db):
    config = sample_nvr_config
    config["nvr_servers"] = [
        {**config["nvr_servers"][0], "id": f"X{i}", "host": f"10.0.0.{i}"}
        for i in range(3)
    ]

    with _patch_scanner(lambda nv_id: _ok_scan(nv_id, abnormal=0)):
        result = batch_scan(config, sample_credentials, memory_db, verbose=False)

    assert result["status"] == "success"
    assert result["ok_nvrs"] == 3
    assert result["failed_nvrs"] == 0
    assert result["failures"] == []


# === 3. failed：全部 NVR 都失敗 ===
def test_all_failure_status(three_nvrs_config, sample_credentials, memory_db):
    def side(nvr_id):
        raise ConnectionError_(f"全軍覆沒 {nvr_id}")

    with _patch_scanner(side):
        result = batch_scan(three_nvrs_config, sample_credentials, memory_db, verbose=False)

    assert result["status"] == "failed"
    assert result["ok_nvrs"] == 0
    assert result["failed_nvrs"] == 3
    assert result["total_cameras"] == 0
    assert result["abnormal_cameras"] == 0


# === 4. per-NVR 失敗不中斷整批：失敗 NVR 的 events 不寫入 DB ===
def test_failures_dont_pollute_db(three_nvrs_config, sample_credentials, memory_db):
    def side(nvr_id):
        if nvr_id == "A":
            return _ok_scan("A", total_cams=5, abnormal=2)
        raise ConnectionError_(f"boom {nvr_id}")

    with _patch_scanner(side):
        result = batch_scan(three_nvrs_config, sample_credentials, memory_db, verbose=False)

    # 只有 A 的 events 寫入
    evs = memory_db.get_events_for_run(result["scan_run_id"])
    assert len(evs) == 1
    assert evs[0]["event_topic"] == "STATE_LONG_FAILED"

    # 3 台 NVR 都被 upsert（失敗也保留設定）
    nvr_count = memory_db._conn.execute(
        "SELECT COUNT(*) FROM nvr_servers"
    ).fetchone()[0]
    assert nvr_count == 3

    # 只有 A 的 cameras 寫入
    cam_count = memory_db._conn.execute(
        "SELECT COUNT(*) FROM cameras"
    ).fetchone()[0]
    assert cam_count == 2  # d1, d2


# === 5. 環境變數覆寫：所有 NVR 用同一組帳密 ===
def test_env_overrides_applied(three_nvrs_config, sample_credentials, memory_db):
    creds = {
        **sample_credentials,
        "username_override": "global-admin",
        "password_override": "global-pass",
    }
    captured = []

    def ctor(nvr_config, **kw):
        captured.append({
            "username": nvr_config["username"],
            "password": nvr_config["password"],
            **kw,
        })
        return MagicMock(scan=MagicMock(return_value=_ok_scan(nvr_config["id"])))

    with patch("batch_scan.AvigilonScanner", side_effect=ctor):
        batch_scan(three_nvrs_config, creds, memory_db, verbose=False)

    assert len(captured) == 3
    for call in captured:
        assert call["username"] == "global-admin"
        assert call["password"] == "global-pass"


# === 6. 每台 NVR 用自己的 AvigilonScanner instance（per-NVR session 隔離）===
def test_per_nvr_scanner_instance(three_nvrs_config, sample_credentials, memory_db):
    instances = []

    def ctor(nvr_config, **kw):
        # 每個 scanner instance 用獨立的 session（模擬 per-NVR session 隔離）
        sess = MagicMock()
        inst = MagicMock(scan=MagicMock(return_value=_ok_scan(nvr_config["id"])))
        inst.session = sess
        instances.append((nvr_config["id"], sess))
        return inst

    with patch("batch_scan.AvigilonScanner", side_effect=ctor):
        batch_scan(three_nvrs_config, sample_credentials, memory_db, verbose=False)

    # 3 個不同 session
    assert len(instances) == 3
    session_ids = {id(s) for _, s in instances}
    assert len(session_ids) == 3  # 全部不同


# === 7. failures 清單結構完整 ===
def test_failures_structure(three_nvrs_config, sample_credentials, memory_db):
    def side(nvr_id):
        raise ConnectionError_(f"無法連線 {nvr_id}")

    with _patch_scanner(side):
        result = batch_scan(three_nvrs_config, sample_credentials, memory_db, verbose=False)

    assert len(result["failures"]) == 3
    for f in result["failures"]:
        assert set(f.keys()) == {"nvr_id", "nvr_name", "type", "error"}
        assert f["type"] == "ConnectionError_"
        assert "無法連線" in f["error"]


# === 8. 空 nvr_servers → ScannerError ===
def test_empty_nvrs_raises(sample_credentials, memory_db):
    from batch_scan import batch_scan as bs
    with pytest.raises(ScannerError, match="為空"):
        bs(
            {"scan_settings": {}, "nvr_servers": []},
            sample_credentials, memory_db, verbose=False,
        )


# === 9. DB 寫入正確（batch stats）===
def test_db_writes_batch_stats(three_nvrs_config, sample_credentials, memory_db):
    def side(nvr_id):
        if nvr_id == "A":
            return _ok_scan("A", total_cams=3, abnormal=1)
        raise ConnectionError_(f"boom {nvr_id}")

    with _patch_scanner(side):
        result = batch_scan(three_nvrs_config, sample_credentials, memory_db, verbose=False)

    row = memory_db._conn.execute(
        "SELECT * FROM scan_runs WHERE id=?", (result["scan_run_id"],)
    ).fetchone()
    assert row["total_nvrs"] == 3
    assert row["ok_nvrs"] == 1
    assert row["failed_nvrs"] == 2
    assert row["total_cameras"] == 3
    assert row["abnormal_cameras"] == 1
    assert row["status"] == "partial"


# === helper：模擬 ConnectionError_ 名稱（避開 builtin）===
class ConnectionError_(Exception):
    pass
