"""
tests/test_web_import.py
========================
Phase 2.5b：CSV / JSON 批次匯入測試。

涵蓋：
    - GET /nvrs/import 顯示表單
    - 下載 CSV / JSON 範本
    - POST 合法 CSV → 寫入成功
    - POST 合法 JSON → 寫入成功
    - POST 不合法（缺欄位、格式錯）→ 顯示錯誤，整批不寫
    - POST 重複 id → 整批 rollback
"""
from __future__ import annotations

import gc
import io
import json
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


@pytest.fixture
def import_app():
    """空 DB Flask app（給匯入測試用）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    # 啟動 schema（即使空 DB 也需要 tables）
    SqliteWriter(db_path)
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
def client(import_app):
    app, _ = import_app
    return app.test_client()


# === 1. GET /nvrs/import ===

def test_import_get_shows_form(client):
    """GET /nvrs/import 200 + 上傳表單。"""
    resp = client.get("/nvrs/import")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "批次匯入" in body
    assert 'type="file"' in body
    assert 'enctype="multipart/form-data"' in body


# === 2. 下載範本 ===

def test_import_template_csv_download(client):
    """GET /nvrs/import/template.csv 200 + 含 header 與範例。"""
    resp = client.get("/nvrs/import/template.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["Content-Type"]
    body = resp.get_data(as_text=True)
    assert "id,name,host,port" in body
    assert "ACC8-P4" in body


def test_import_template_json_download(client):
    """GET /nvrs/import/template.json 200 + JSON array。"""
    resp = client.get("/nvrs/import/template.json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["Content-Type"]
    data = json.loads(resp.get_data(as_text=True))
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "id" in data[0]


# === 3. POST 合法 CSV → 寫入 ===

def test_import_post_csv_success(client, import_app):
    """POST 合法 CSV → flash success + redirect + DB 有資料。"""
    app, db_path = import_app
    csv_content = (
        "id,name,host,port,username,password,verify_ssl,site_id,tags\n"
        "CSV-1,NVR 一,10.0.0.1,8443,api_reader,secret,0,,branch;taipei\n"
        "CSV-2,NVR 二,10.0.0.2,8443,api_reader,secret,0,,branch;taichung\n"
    )
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(csv_content.encode("utf-8")), "nvrs.csv"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302  # redirect 回 nvrs_list

    # DB 確認
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT nvr_id, name FROM nvr_servers ORDER BY nvr_id").fetchall()
    conn.close()
    assert ("CSV-1", "NVR 一") in rows
    assert ("CSV-2", "NVR 二") in rows


def test_import_post_csv_tab_delimiter(client, import_app):
    """Tab 分隔的 CSV 也能解析（Excel 另存成 TSV 的場景）。

    Sniffer 自動偵測 delimiter；不論 , 或 \\t 都吃得進去。
    不支援 `;`（歐洲 Excel）—— tags 欄位內部已用 `;` 分隔會衝突。
    """
    app, db_path = import_app
    csv_content = (
        "id\tname\thost\tport\tusername\tpassword\tverify_ssl\tsite_id\ttags\n"
        "TSV-1\tNVR 一\t10.0.0.1\t8443\tapi_reader\tsecret\t0\t\tbranch;taipei\n"
        "TSV-2\tNVR 二\t10.0.0.2\t8443\tapi_reader\tsecret\t0\t\tbranch;taichung\n"
    )
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(csv_content.encode("utf-8")), "nvrs.csv"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT nvr_id, name FROM nvr_servers ORDER BY nvr_id").fetchall()
    conn.close()
    assert ("TSV-1", "NVR 一") in rows
    assert ("TSV-2", "NVR 二") in rows


def test_import_post_json_success(client, import_app):
    """POST 合法 JSON → 寫入成功。"""
    app, db_path = import_app
    json_data = [
        {"id": "JSON-1", "name": "JSON NVR 一", "host": "10.0.0.10",
         "port": 8443, "username": "u", "password": "p",
         "verify_ssl": False, "site_id": "A", "tags": ["hq"]},
        {"id": "JSON-2", "name": "JSON NVR 二", "host": "10.0.0.11",
         "port": 8443, "username": "u", "password": "p"},
    ]
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(json.dumps(json_data).encode("utf-8")), "nvrs.json"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT nvr_id FROM nvr_servers ORDER BY nvr_id").fetchall()
    conn.close()
    nvr_ids = [r[0] for r in rows]
    assert "JSON-1" in nvr_ids
    assert "JSON-2" in nvr_ids


# === 4. POST 不合法 → 整批不寫 ===

def test_import_post_csv_missing_id_column(client, import_app):
    """CSV 缺 id 欄位 → 顯示錯誤，不寫入。"""
    app, db_path = import_app
    csv_content = "name,host\nNoId,10.0.0.1\n"
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(csv_content.encode("utf-8")), "bad.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "id" in body  # 錯誤訊息提到 id
    # DB 沒寫入
    import sqlite3
    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0]
    conn.close()
    assert cnt == 0


def test_import_post_csv_missing_required_field(client, import_app):
    """CSV 第 2 行缺 host → 整批不寫。"""
    app, db_path = import_app
    csv_content = (
        "id,name,host,port,username,password,verify_ssl,site_id,tags\n"
        "OK-1,OK NVR,10.0.0.1,8443,api_reader,secret,0,,\n"
        "BAD-1,Bad NVR,,8443,api_reader,secret,0,,\n"  # host 缺
    )
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(csv_content.encode("utf-8")), "partial.csv"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "host" in body.lower()  # 錯誤訊息提到 host
    # DB 沒寫入（整批 rollback）
    import sqlite3
    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0]
    conn.close()
    assert cnt == 0, "驗證錯誤應整批 rollback"


def test_import_post_duplicate_id_upserts(client, import_app):
    """Phase 2.5c 起：id 已存在 → upsert（更新），不再整批 rollback。

    上傳 NEW-1（新）+ DUP-1（DB 內已有）→ 應：
    - NEW-1 新增
    - DUP-1 更新（name 改為新值）
    - 整批成功（302 redirect）
    """
    app, db_path = import_app
    # 先 seed DUP-1
    w = SqliteWriter(db_path)
    w.upsert_nvr({"id": "DUP-1", "name": "舊名稱", "host": "1.1.1.1",
                  "port": 8443, "username": "u", "password": "p"})
    del w

    # 上傳 CSV 含 DUP-1（重複 → 應更新）+ NEW-1（新增）
    csv_content = (
        "id,name,host,port,username,password,verify_ssl,site_id,tags\n"
        "NEW-1,New NVR,10.0.0.1,8443,api_reader,secret,0,,\n"
        "DUP-1,新名稱,10.0.0.2,8443,api_reader,new-pw,0,,\n"  # 重複 → 更新
    )
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(csv_content.encode("utf-8")), "dup.csv"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302  # 整批成功

    # DB 確認：兩筆都在，DUP-1 的 name 已更新
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT nvr_id, name FROM nvr_servers ORDER BY nvr_id"
    ).fetchall()
    conn.close()
    by_id = {r[0]: r[1] for r in rows}
    assert by_id["NEW-1"] == "New NVR"
    assert by_id["DUP-1"] == "新名稱"  # 已被更新


def test_import_post_upsert_keeps_password_when_empty(client, import_app):
    """Upsert 時 password 留空 → 保留原密碼（避免無意清空）。"""
    app, db_path = import_app
    # seed DUP-1 含密碼
    w = SqliteWriter(db_path)
    w.upsert_nvr({"id": "PW-1", "name": "N", "host": "1.1.1.1",
                  "port": 8443, "username": "u", "password": "ORIG-PW"})
    del w

    # 上傳新 name 但 password 留空
    csv_content = (
        "id,name,host,port,username,password,verify_ssl,site_id,tags\n"
        "PW-1,新名字,1.1.1.1,8443,u,,0,,\n"  # password 空
    )
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(csv_content.encode("utf-8")), "pw.csv"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert resp.status_code == 302

    # 確認 password 仍是原值
    import sqlite3
    conn = sqlite3.connect(db_path)
    pw = conn.execute(
        "SELECT password FROM nvr_servers WHERE nvr_id = 'PW-1'"
    ).fetchone()[0]
    name = conn.execute(
        "SELECT name FROM nvr_servers WHERE nvr_id = 'PW-1'"
    ).fetchone()[0]
    conn.close()
    assert name == "新名字"  # name 有更新
    assert pw == "ORIG-PW"  # password 保留原值


def test_import_post_no_file_redirects(client):
    """沒選檔案 → flash + redirect 回 /nvrs/import。"""
    resp = client.post("/nvrs/import", data={},
                       content_type="multipart/form-data",
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "/nvrs/import" in resp.headers["Location"]


def test_import_post_invalid_json_rolls_back(client, import_app):
    """JSON 語法錯 → 顯示錯誤，不寫入。"""
    app, db_path = import_app
    resp = client.post("/nvrs/import", data={
        "file": (io.BytesIO(b"{not valid json"), "bad.json"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "JSON" in body
    # DB 沒寫入
    import sqlite3
    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM nvr_servers").fetchone()[0]
    conn.close()
    assert cnt == 0