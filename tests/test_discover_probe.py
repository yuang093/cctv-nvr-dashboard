"""
tests/test_discover_probe.py
=============================
Phase 2.8（Arisan）Phase #6：CIDR 探索網段實作測試。
- 1 unit test：CIDR /16 拒絕
- 2 integration tests：probe 寫入結果 + 跳過既有 NVR IP
"""
from __future__ import annotations

import gc
import json as _json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.sqlite_writer import SqliteWriter


@pytest.fixture
def discover_app(monkeypatch):
    """乾淨 DB + Flask app（不 seed 任何 NVR）。

    阻止 route 啟動的 background thread（測試改用同步呼叫 run_discovery_for_session）。
    注意：不能 monkeypatch threading.Thread.start（會破壞 ThreadPoolExecutor）。
    """
    import web.app as _app
    monkeypatch.setattr(_app, "_start_probe_thread", lambda *a, **kw: None)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    from web.app import create_app
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path
    del w, app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def client(discover_app):
    app, _ = discover_app
    return app.test_client()


def _extract_session_id(resp) -> int:
    """從 POST redirect 的 Location header 解出 session_id。"""
    loc = resp.headers["Location"]
    return int(loc.rsplit("/", 1)[-1])


# === 1. Unit test：CIDR /16 拒絕 ===
def test_expand_cidr_rejects_too_large_prefix():
    """CIDR prefix < /16 必須 raise ValueError（防 DoS）。"""
    from web.discover import expand_cidr
    # /8 太大（16M IPs）必須拒
    with pytest.raises(ValueError, match="/16|過大"):
        expand_cidr("10.0.0.0/8")
    # /12 也太大
    with pytest.raises(ValueError, match="/16|過大"):
        expand_cidr("172.16.0.0/12")


# === 1b. Unit test：合法 CIDR 展開正確 ===
def test_expand_cidr_expands_correctly():
    """/30 應展開為 2 個 host IP（排除 network/broadcast）。"""
    from web.discover import expand_cidr
    ips = expand_cidr("192.168.1.0/30")
    assert ips == ["192.168.1.1", "192.168.1.2"]


# === 2. Integration test：probe 結果寫入 discover_sessions ===
def test_discover_post_runs_probe_and_writes_results(discover_app, client, monkeypatch):
    """POST /devices/discover 建 session → 同步觸發 probe → 結果寫進 results_json。"""
    app, db_path = discover_app
    probed_urls: list[str] = []

    def fake_get(url, timeout, verify):
        probed_urls.append(url)
        if "1.1.1.1" in url:
            r = MagicMock()
            r.headers = {"Server": "Avigilon Control Center"}
            r.status_code = 302
            return r
        else:
            r = MagicMock()
            r.headers = {"Server": "nginx"}
            r.status_code = 200
            return r

    monkeypatch.setattr("web.discover.requests.get", fake_get)

    # 1. POST 建 session（route 用 thread，會馬上 redirect）
    resp = client.post("/devices/discover", data={"cidr": "1.1.1.0/30", "port": "8443"})
    assert resp.status_code == 302
    session_id = _extract_session_id(resp)

    # 2. 同步觸發 probe（測試環境不靠 thread 等，避免 race）
    from web.discover import run_discovery_for_session
    run_discovery_for_session(db_path, session_id)

    # 3. 檢查 DB
    from web import db as webdb
    sess = webdb._connect(db_path).execute(
        "SELECT results_json, status FROM discover_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    assert sess is not None
    assert sess["status"] == "completed"
    results = _json.loads(sess["results_json"])
    # /30 → 2 host IP（1.1.1.1, 1.1.1.2）
    assert len(results) == 2
    by_ip = {r["ip"]: r for r in results}
    assert by_ip["1.1.1.1"]["is_avigilon"] is True
    assert by_ip["1.1.1.2"]["is_avigilon"] is False
    # 確認真的有 probe（兩個都打到）
    assert len(probed_urls) == 2


# === 3. Integration test：跳過既有 NVR IP ===
def test_discover_post_skips_existing_nvr_ips(discover_app, client, monkeypatch):
    """既有 NVR 的 IP 不 probe（標記 skipped=True）。"""
    app, db_path = discover_app
    # Seed 一台 NVR 在 192.168.1.100
    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "NVR-X", "name": "X 分店", "host": "192.168.1.100",
        "port": 8443, "username": "u", "password": "p",
    })
    w._conn.close()

    probed_ips: list[str] = []

    def fake_get(url, timeout, verify):
        # 抓出 IP 記下來
        for ip in ("192.168.1.97", "192.168.1.98", "192.168.1.99",
                   "192.168.1.101", "192.168.1.102"):
            if ip in url:
                probed_ips.append(ip)
        r = MagicMock()
        r.headers = {"Server": "Avigilon Control Center"}
        r.status_code = 302
        return r

    monkeypatch.setattr("web.discover.requests.get", fake_get)

    # /29 from 192.168.1.96：network=.96, broadcast=.103, hosts=.97-.102
    # seed NVR=.100 在 hosts 內 → 應 skip
    resp = client.post("/devices/discover", data={"cidr": "192.168.1.96/29", "port": "8443"})
    assert resp.status_code == 302
    session_id = _extract_session_id(resp)

    # 同步觸發 probe
    from web.discover import run_discovery_for_session
    run_discovery_for_session(db_path, session_id)

    # 192.168.1.100 沒被 probe
    assert "192.168.1.100" not in probed_ips
    # 其他 5 個 host 都 probe
    assert len(probed_ips) == 5

    from web import db as webdb
    sess = webdb._connect(db_path).execute(
        "SELECT results_json FROM discover_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    results = _json.loads(sess["results_json"])
    skip_100 = [r for r in results if r["ip"] == "192.168.1.100"]
    assert len(skip_100) == 1
    assert skip_100[0].get("skipped") is True
    assert skip_100[0].get("reason") == "existing_nvr"
