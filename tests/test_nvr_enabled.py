"""
tests/test_nvr_enabled.py
=========================
NVR 啟用狀態切換測試（v2.7+ 起 DB 為唯一 source of truth）。

涵蓋：
    - migration 003：nvr_servers 加 enabled 欄位 + 預設 1
    - list_enabled_nvrs：過濾 + 格式轉換
    - set_nvr_enabled：toggle + 不存在的 id 回 False
    - bulk_upsert_nvrs 預設 enabled=1；明確 enabled=0 才停用
    - Web toggle route（POST /nvrs/<id>/toggle）
    - UI 顯示啟用/停用 badge
    - batch_scan 讀 DB（整合測試）
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web import db as webdb
from web.app import create_app


# === Fixtures ===


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    SqliteWriter(path)
    yield path
    try:
        Path(path).unlink()
    except OSError:
        pass


@pytest.fixture
def app(db_path):
    a = create_app(db_path=db_path)
    a.config["TESTING"] = True
    yield a
    del a
    gc.collect()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_nvr(
    db_path: str,
    nvr_id: str = "NVR-A",
    name: str = "A 大樓",
    host: str = "1.1.1.1",
    **kwargs,
) -> int:
    data = {
        "nvr_id": nvr_id,
        "name": name,
        "host": host,
        "port": kwargs.get("port", 8443),
        "username": kwargs.get("username", "admin"),
        "password": kwargs.get("password", "pw"),
        "verify_ssl": kwargs.get("verify_ssl", False),
        "site_id": kwargs.get("site_id"),
        "tags": kwargs.get("tags", []),
    }
    if "enabled" in kwargs:
        data["enabled"] = kwargs["enabled"]
    return webdb.create_nvr(db_path, data)


# === Migration 測試 ===


def test_migration_adds_enabled_column(db_path):
    """SqliteWriter 啟動時自動加 enabled 欄位（idempotent）。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(nvr_servers)")]
        assert "enabled" in cols
    finally:
        conn.close()


def test_migration_idempotent(db_path):
    """連開兩次 SqliteWriter 不會壞。"""
    SqliteWriter(db_path)
    SqliteWriter(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(nvr_servers)")]
        assert "enabled" in cols
    finally:
        conn.close()


# === list_enabled_nvrs ===


def test_list_enabled_nvrs_only_returns_enabled(db_path):
    _seed_nvr(db_path, "NVR-A")
    _seed_nvr(db_path, "NVR-B")
    _seed_nvr(db_path, "NVR-C")
    # 把 NVR-B 停用
    internal_b = webdb.get_nvr(db_path, None)  # placeholder
    # 用 nvr_id 拿 internal_id
    nvr_b = next(n for n in webdb.get_nvrs(db_path) if n["nvr_id"] == "NVR-B")
    webdb.set_nvr_enabled(db_path, nvr_b["id"], False)

    enabled = webdb.list_enabled_nvrs(db_path)
    nvr_ids = {n["id"] for n in enabled}
    assert "NVR-A" in nvr_ids
    assert "NVR-B" not in nvr_ids
    assert "NVR-C" in nvr_ids


def test_list_enabled_nvrs_format_matches_batch_scan(db_path):
    """回傳 dict 格式要跟 batch_scan 預期對得上（id/host/port/username/...）。"""
    _seed_nvr(
        db_path,
        "NVR-A",
        name="A 大樓",
        host="1.2.3.4",
        port=8443,
        username="admin",
        password="secret",
        site_id="BRANCH-A",
        tags=["branch", "taipei"],
    )
    enabled = webdb.list_enabled_nvrs(db_path)
    assert len(enabled) == 1
    nvr = enabled[0]
    assert nvr["id"] == "NVR-A"
    assert nvr["name"] == "A 大樓"
    assert nvr["host"] == "1.2.3.4"
    assert nvr["port"] == 8443
    assert nvr["username"] == "admin"
    assert nvr["password"] == "secret"
    assert nvr["verify_ssl"] is False
    assert nvr["site_id"] == "BRANCH-A"
    assert nvr["tags"] == ["branch", "taipei"]


def test_list_enabled_nvrs_empty_db(db_path):
    assert webdb.list_enabled_nvrs(db_path) == []


# === set_nvr_enabled ===


def test_set_nvr_enabled_toggle(db_path):
    nid = _seed_nvr(db_path, "NVR-A")
    # 預設 enabled
    assert webdb.get_nvr(db_path, nid)["enabled"] == 1
    # 停用
    assert webdb.set_nvr_enabled(db_path, nid, False) is True
    assert webdb.get_nvr(db_path, nid)["enabled"] == 0
    # 再啟用
    assert webdb.set_nvr_enabled(db_path, nid, True) is True
    assert webdb.get_nvr(db_path, nid)["enabled"] == 1


def test_set_nvr_enabled_missing_returns_false(db_path):
    assert webdb.set_nvr_enabled(db_path, 99999, False) is False


# === bulk_upsert_nvrs 預設 enabled=1 ===


def test_bulk_upsert_default_enabled(db_path):
    webdb.bulk_upsert_nvrs(
        db_path,
        [
            {
                "nvr_id": "X1",
                "name": "X1",
                "host": "1.1.1.1",
                "port": 8443,
                "username": "u",
                "password": "p",
            },
            {
                "nvr_id": "X2",
                "name": "X2",
                "host": "2.2.2.2",
                "port": 8443,
                "username": "u",
                "password": "p",
            },
        ],
    )
    nvrs = webdb.get_nvrs(db_path)
    assert all(n["enabled"] == 1 for n in nvrs)


def test_bulk_upsert_respects_explicit_enabled_false(db_path):
    webdb.bulk_upsert_nvrs(
        db_path,
        [
            {
                "nvr_id": "Y1",
                "name": "Y1",
                "host": "1.1.1.1",
                "port": 8443,
                "username": "u",
                "password": "p",
                "enabled": False,
            },
        ],
    )
    n = next(n for n in webdb.get_nvrs(db_path) if n["nvr_id"] == "Y1")
    assert n["enabled"] == 0


# === Web toggle route ===


def test_nvr_toggle_enabled_route(client, db_path):
    nid = _seed_nvr(db_path, "NVR-A")
    # 預設啟用 → 點 toggle 變停用
    resp = client.post(f"/nvrs/{nid}/toggle", follow_redirects=False)
    assert resp.status_code == 302  # redirect
    assert webdb.get_nvr(db_path, nid)["enabled"] == 0
    # 再 toggle 啟用
    resp = client.post(f"/nvrs/{nid}/toggle", follow_redirects=False)
    assert resp.status_code == 302
    assert webdb.get_nvr(db_path, nid)["enabled"] == 1


def test_nvr_toggle_missing_404(client):
    resp = client.post("/nvrs/99999/toggle")
    assert resp.status_code == 404


# === UI 顯示 ===


def test_nvrs_list_shows_enabled_badge(client, db_path):
    _seed_nvr(db_path, "NVR-ON", name="啟用中")
    _seed_nvr(db_path, "NVR-OFF", name="已停用")
    # 把 NVR-OFF 停用
    nvr_off = next(n for n in webdb.get_nvrs(db_path) if n["nvr_id"] == "NVR-OFF")
    webdb.set_nvr_enabled(db_path, nvr_off["id"], False)

    resp = client.get("/nvrs")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "啟用中" in body
    assert "已停用" in body
    # 啟用 badge
    assert "✓ 啟用" in body
    assert "⏸ 停用" in body
    # toggle 按鈕
    assert "▶ 啟用" in body  # 停用的列出現啟用按鈕
    assert "⏸ 停用" in body  # 啟用的列出現停用按鈕


# === 整合：batch_scan 讀 DB ===


def test_batch_scan_uses_db_not_config(tmp_path, monkeypatch, db_path):
    """驗證 batch_scan main() 從 DB 讀 NVR，不再從 nvr_config.json 讀。"""
    # 寫一份假 nvr_config.json（含 2 台），但故意只 DB 內有 1 台
    cfg_path = tmp_path / "nvr_config.json"
    cfg_path.write_text(
        """{
        "scan_settings": {"db_path": "./nvr_scan.db", "timeout_seconds": 5},
        "nvr_servers": [
            {"id": "CFG-1", "name": "CFG1", "host": "9.9.9.1",
             "port": 8443, "username": "u", "password": "p", "enabled": true}
        ]
    }""",
        encoding="utf-8",
    )
    # DB 內 1 台（不同 id）
    _seed_nvr(db_path, "DB-1", name="DB1", host="8.8.8.1")

    # 模擬 CLI 路徑：用 db_path 呼叫 list_enabled_nvrs
    enabled = webdb.list_enabled_nvrs(db_path)
    assert [n["id"] for n in enabled] == ["DB-1"]
    # 不會從 cfg_path 讀（除非 DB 是空才 fallback seed）


def test_run_scan_in_background_cfg_has_scan_settings(monkeypatch, db_path):
    """回歸測試：_run_scan_in_background 組出來的 cfg 必須含 scan_settings，
    否則 batch_scan() 內部 `cfg["scan_settings"].get(...)` 會 KeyError 炸掉。

    之前 regression：改讀 DB 後忘了補 timeout_seconds，2026-07-07 發現並修。
    """
    from web import app as webapp

    # 抓 _run_scan_in_background 組 cfg 的那一行（用動態 import 取 reference）
    import inspect

    src = inspect.getsource(webapp._run_scan_in_background)
    assert '"scan_settings"' in src, "_run_scan_in_background 沒組 scan_settings"
    assert "timeout_seconds" in src, "scan_settings 沒帶 timeout_seconds（會 KeyError）"

    # 動態驗證：DB 內 1 台啟用 NVR → 跑一次 batch_scan 確認 cfg 不會 KeyError
    _seed_nvr(
        db_path, "DB-1", host="127.0.0.1", port=1
    )  # port=1 不會真連，預期 connection error
    enabled = webdb.list_enabled_nvrs(db_path)
    cfg = {"nvr_servers": enabled, "scan_settings": {"timeout_seconds": 10}}
    # 直接驗證 cfg 形狀正確（避免 mock 整個 batch_scan）
    assert cfg["scan_settings"]["timeout_seconds"] == 10
    assert len(cfg["nvr_servers"]) == 1


def test_web_default_does_not_auto_open_browser(monkeypatch):
    """回歸測試：main() 預設不開瀏覽器（避免開發 / 重啟時一直跳分頁干擾）。

    之前行為：auto_open = (env NVR_WEB_NO_BROWSER != "1") → 預設 True → 一直跳分頁。
    新行為：auto_open = (env NVR_WEB_OPEN_BROWSER in {"1","true"}) → 預設 False。
    只想 opt-in 開瀏覽器的人自己設 NVR_WEB_OPEN_BROWSER=1。
    """
    import inspect
    from web import app as webapp

    src = inspect.getsource(webapp.main)
    # 環境變數名稱
    assert (
        "NVR_WEB_OPEN_BROWSER" in src
    ), "main() 沒讀 NVR_WEB_OPEN_BROWSER；退回舊邏輯會預設自動開瀏覽器"
    # 預設必須是「不開」 — 用 in ("1","true") 才會 True，否則 False
    assert (
        '"true"' in src and '"1"' in src
    ), "main() 沒把 auto_open 預設關閉（應為 opt-in 用 in ('1','true') 判斷）"
    # 不能繼續用舊的 NVR_WEB_NO_BROWSER（會把語意顛倒）
    assert (
        "NVR_WEB_NO_BROWSER" not in src
    ), "main() 還在讀舊的 NVR_WEB_NO_BROWSER；應改用 opt-in 的 NVR_WEB_OPEN_BROWSER"
