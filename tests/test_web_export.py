"""
tests/test_web_export.py
========================
Phase 2.5c：CSV / JSON 匯出測試。

涵蓋：
    - GET /nvrs/export.csv 200 + Content-Disposition + UTF-8 BOM
    - GET /nvrs/export.json 200 + JSON array
    - 多筆 NVR 全匯出
    - CSV header / 欄位順序正確
    - Round-trip：匯出 → 修改 → 匯入 → DB 一致
    - 空 DB 匯出 = 只剩 header / 空 array
    - 密碼以明碼回傳（給 round-trip 用）
"""
from __future__ import annotations

import csv
import gc
import io
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


# === Fixture ===

@pytest.fixture
def export_app():
    """空 DB Flask app（給 export 測試用）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    SqliteWriter(db_path)  # 啟動 schema
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path
    del app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


@pytest.fixture
def client(export_app):
    app, _ = export_app
    return app.test_client()


@pytest.fixture
def seeded_db(export_app):
    """灌 2 台 NVR（含中文 tags）到 DB。"""
    app, db_path = export_app
    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "ACC8-P4",
        "name": "總部主 NVR",
        "host": "192.168.133.141",
        "port": 8443,
        "username": "administrator",
        "password": "SECRET-123",
        "verify_ssl": False,
        "site_id": "HQ",
        "tags": ["branch", "台北"],
    })
    w.upsert_nvr({
        "id": "BRANCH-B",
        "name": "B 分店",
        "host": "192.168.2.100",
        "port": 8443,
        "username": "api_reader",
        "password": "PW-B",
        "verify_ssl": True,
        "site_id": "",
        "tags": ["branch", "taichung"],
    })
    del w
    return app, db_path


# === 1. 匯出 endpoint 基本行為 ===

def test_export_csv_200_and_disposition(seeded_db, client):
    """CSV 匯出 200 + Content-Disposition attachment + UTF-8 BOM。"""
    resp = client.get("/nvrs/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["Content-Type"]
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "nvr_export_" in resp.headers["Content-Disposition"]
    assert ".csv" in resp.headers["Content-Disposition"]
    body = resp.get_data()
    # UTF-8 BOM 在最前面
    assert body.startswith(b"\xef\xbb\xbf")


def test_export_json_200_and_disposition(seeded_db, client):
    """JSON 匯出 200 + Content-Disposition attachment。"""
    resp = client.get("/nvrs/export.json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["Content-Type"]
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "nvr_export_" in resp.headers["Content-Disposition"]
    assert ".json" in resp.headers["Content-Disposition"]
    data = json.loads(resp.get_data(as_text=True))
    assert isinstance(data, list)


# === 2. 匯出內容正確 ===

def test_export_csv_content(seeded_db, client):
    """CSV 內容含正確 header + 兩筆資料（含中文 tags）。"""
    resp = client.get("/nvrs/export.csv")
    # 跳過 BOM
    body = resp.get_data(as_text=True).lstrip("﻿")
    reader = csv.DictReader(io.StringIO(body))
    assert reader.fieldnames == [
        "id", "name", "host", "port", "username", "password",
        "verify_ssl", "site_id", "tags",
    ]
    rows = list(reader)
    assert len(rows) == 2
    # 找出 ACC8-P4 那筆
    by_id = {r["id"]: r for r in rows}
    assert "ACC8-P4" in by_id
    acc = by_id["ACC8-P4"]
    assert acc["name"] == "總部主 NVR"
    assert acc["host"] == "192.168.133.141"
    assert acc["port"] == "8443"
    assert acc["username"] == "administrator"
    assert acc["password"] == "SECRET-123"  # 明碼
    assert acc["verify_ssl"] == "0"  # bool → 0/1
    assert acc["site_id"] == "HQ"
    assert acc["tags"] == "branch;台北"  # list → ";" 分隔


def test_export_json_content(seeded_db, client):
    """JSON 內容：正確欄位 + bool / list 型別。"""
    resp = client.get("/nvrs/export.json")
    data = json.loads(resp.get_data(as_text=True))
    assert len(data) == 2
    by_id = {r["id"]: r for r in data}
    acc = by_id["ACC8-P4"]
    assert acc["name"] == "總部主 NVR"
    assert acc["host"] == "192.168.133.141"
    assert acc["port"] == 8443
    assert acc["username"] == "administrator"
    assert acc["password"] == "SECRET-123"  # 明碼
    assert acc["verify_ssl"] is False
    assert acc["site_id"] == "HQ"
    assert acc["tags"] == ["branch", "台北"]
    # 第二台
    b = by_id["BRANCH-B"]
    assert b["verify_ssl"] is True
    assert b["site_id"] is None  # 空字串 → None
    assert b["tags"] == ["branch", "taichung"]


# === 3. 空 DB 匯出 ===

def test_export_csv_empty_db(client):
    """空 DB：CSV 只有 header。"""
    resp = client.get("/nvrs/export.csv")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True).lstrip("﻿")
    reader = csv.DictReader(io.StringIO(body))
    assert list(reader) == []


def test_export_json_empty_db(client):
    """空 DB：JSON = []。"""
    resp = client.get("/nvrs/export.json")
    assert resp.status_code == 200
    assert json.loads(resp.get_data(as_text=True)) == []


# === 4. Round-trip：匯出 → 匯入 → DB 一致 ===

def test_export_csv_round_trip_to_import(seeded_db):
    """從 DB A 匯出 CSV → 灌到 DB B → DB B 內容一致。"""
    app_a, db_a = seeded_db
    # 用新 client 取 DB A 的 CSV
    client_a = app_a.test_client()
    csv_resp = client_a.get("/nvrs/export.csv")
    csv_bytes = csv_resp.get_data()

    # 建 DB B + Flask
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_b = f.name
    try:
        SqliteWriter(db_b)
        app_b = create_app(db_path=db_b)
        client_b = app_b.test_client()

        # 把 CSV 匯入 DB B
        imp = client_b.post("/nvrs/import", data={
            "file": (io.BytesIO(csv_bytes), "from_a.csv"),
        }, content_type="multipart/form-data", follow_redirects=False)
        assert imp.status_code == 302  # success redirect

        # DB B 應該有 2 台 NVR，欄位一致
        conn = sqlite3.connect(db_b)
        rows = conn.execute(
            "SELECT nvr_id, name, host, port, username, password, "
            "verify_ssl, site_id, tags FROM nvr_servers ORDER BY nvr_id"
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        # ACC8-P4
        acc = next(r for r in rows if r[0] == "ACC8-P4")
        assert acc[1] == "總部主 NVR"
        assert acc[2] == "192.168.133.141"
        assert acc[3] == 8443
        assert acc[4] == "administrator"
        assert acc[5] == "SECRET-123"
        assert acc[6] == 0
        assert acc[7] == "HQ"
        assert json.loads(acc[8]) == ["branch", "台北"]
    finally:
        Path(db_b).unlink(missing_ok=True)


def test_export_json_round_trip_to_import(seeded_db):
    """從 DB A 匯出 JSON → 灌到 DB B → DB B 內容一致。"""
    app_a, db_a = seeded_db
    client_a = app_a.test_client()
    json_resp = client_a.get("/nvrs/export.json")
    json_bytes = json_resp.get_data()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_b = f.name
    try:
        SqliteWriter(db_b)
        app_b = create_app(db_path=db_b)
        client_b = app_b.test_client()

        imp = client_b.post("/nvrs/import", data={
            "file": (io.BytesIO(json_bytes), "from_a.json"),
        }, content_type="multipart/form-data", follow_redirects=False)
        assert imp.status_code == 302

        conn = sqlite3.connect(db_b)
        n = conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0]
        conn.close()
        assert n == 2
    finally:
        Path(db_b).unlink(missing_ok=True)


# === 5. 密碼以明碼回傳 ===

def test_export_csv_password_is_plaintext(seeded_db, client):
    """CSV 密碼欄位是明碼（給 round-trip 用）。"""
    resp = client.get("/nvrs/export.csv")
    body = resp.get_data(as_text=True)
    assert "SECRET-123" in body
    assert "PW-B" in body


def test_export_json_password_is_plaintext(seeded_db, client):
    """JSON 密碼欄位是明碼（給 round-trip 用）。"""
    resp = client.get("/nvrs/export.json")
    data = json.loads(resp.get_data(as_text=True))
    passwords = {r["password"] for r in data}
    assert "SECRET-123" in passwords
    assert "PW-B" in passwords