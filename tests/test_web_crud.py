"""
tests/test_web_crud.py
======================
Phase 2.5a：Web UI NVR CRUD 的整合測試。

策略：tempfile 開檔案 DB → 灌 seed → 用 Flask test client 跑 CRUD routes → 檢查：
    - HTTP status code
    - 頁面內容
    - DB 實際寫入 / 刪除 / 更新

涵蓋：
    - 清單：分頁、搜尋、計數
    - 新增：GET form、POST 成功、POST 驗證錯誤、POST 重複 ID
    - 編輯：GET form（密碼永遠清空）、POST 更新 name、POST 密碼留空=不變、POST 密碼填寫=變更
    - 刪除：POST 刪 NVR+cameras，保留 events/scan_runs
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


# === 共用 fixture：灌 1 台 NVR + 1 台 camera + 1 個 event ===
@pytest.fixture
def crud_web_app():
    """建立 Flask app + 灌好測試資料的檔案 DB（含 1 台 NVR + cameras + events）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    # 1. upsert_nvr 不需要 scan_run（在 begin 之前）
    nvr_a_id = w.upsert_nvr({
        "id": "NVR-A", "name": "A 分店", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
        "tags": ["branch", "taipei"],
    })
    # 2. begin_scan_run → 開 transaction
    rid = w.begin_scan_run("2026-07-01T00:00:00Z")
    # 3. upsert_cameras 需要 scan_run
    w.upsert_cameras(nvr_a_id, {
        "d1": {"name": "大門", "connection_state": "CONNECTED", "available": True},
        "d2": {"name": "停車場", "connection_state": "LONG_FAILED", "available": False},
    })
    # 4. insert_events
    w.insert_events(rid, nvr_a_id, [{
        "eventId": "e1", "deviceId": "d2",
        "eventTopics": ["STATE_LONG_FAILED"],
        "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-07-01T00:00:00Z",
    }])
    # 5. finish_scan_run → commit
    w.finish_scan_run(
        rid, finished_at="2026-07-01T00:01:00Z",
        status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 1,
               "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0},
    )

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    yield app, db_path, nvr_a_id

    del w, app
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass  # Windows file lock 偶爾，無視


@pytest.fixture
def client(crud_web_app):
    app, _, _ = crud_web_app
    return app.test_client()


# === 1. 清單頁 ===

def test_nvr_list_default_shows_seeded(client):
    """GET /nvrs 200 + 看到 seed 的 NVR-A。"""
    resp = client.get("/nvrs")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "NVR-A" in body
    assert "A 分店" in body
    assert "10.0.0.1" in body
    # 新增按鈕
    assert "+ 新增 NVR" in body
    # 編輯/刪除按鈕
    assert "編輯" in body
    assert "刪除" in body


def test_nvr_list_search_q(client):
    """GET /nvrs?q=ACC8 過濾（這裡 NVR-A 不會匹配 ACC8，應為空）。"""
    resp = client.get("/nvrs?q=ACC8")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "NVR-A" not in body


def test_nvr_list_search_q_match(client):
    """GET /nvrs?q=NVR-A 應匹配。"""
    resp = client.get("/nvrs?q=NVR-A")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "NVR-A" in body


# === 2. 新增頁 ===

def test_nvr_new_get_shows_form(client):
    """GET /nvrs/new 200 + 表單欄位齊全。"""
    resp = client.get("/nvrs/new")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for field in ("nvr_id", "name", "host", "port", "username", "password",
                  "verify_ssl", "site_id", "tags"):
        assert f'name="{field}"' in body, f"缺少欄位 {field}"


def test_nvr_new_post_creates_and_redirects(client):
    """POST /nvrs/new 建立成功 → 跳轉 + DB 有新 NVR。"""
    resp = client.post("/nvrs/new", data={
        "nvr_id": "NEW-NVR",
        "name": "新 NVR",
        "host": "192.168.1.100",
        "port": "8443",
        "username": "api_reader",
        "password": "secret",
        "verify_ssl": "on",
        "site_id": "HQ",
        "tags": "branch;hsinchu",
    }, follow_redirects=False)
    assert resp.status_code == 302  # redirect to nvrs_list
    assert "/nvrs" in resp.headers["Location"]

    # 重新查 DB 確認
    resp2 = client.get("/nvrs?q=NEW-NVR")
    body = resp2.get_data(as_text=True)
    assert "NEW-NVR" in body
    assert "新 NVR" in body


def test_nvr_new_post_validation_error_renders_form_with_input(client):
    """POST /nvrs/new 缺 host → 顯示錯誤，不寫入。"""
    resp = client.post("/nvrs/new", data={
        "nvr_id": "BAD-NVR",
        "name": "Bad NVR",
        # host 缺
        "port": "8443",
        "username": "u",
        "password": "p",
    })
    # 不 redirect，留在 form 頁
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Host 必填" in body or "alert-danger" in body
    assert "BAD-NVR" in body  # 使用者輸入保留
    # DB 沒寫入：清單頁表格不應有 BAD-NVR row（搜尋框 value 可能含 BAD-NVR，所以用 marker）
    resp2 = client.get("/nvrs")
    body2 = resp2.get_data(as_text=True)
    # 搜尋框內的 BAD-NVR 是 value="BAD-NVR"，但 NVR 清單表不該有 BAD-NVR
    # 用 tbody 內是否有 <code>BAD-NVR</code> 來判斷
    import re
    tbody_match = re.search(r"<tbody>.*?</tbody>", body2, re.DOTALL)
    assert tbody_match is not None
    assert "BAD-NVR" not in tbody_match.group(0)


def test_nvr_new_post_duplicate_id_rejected(client):
    """POST 重複 nvr_id (NVR-A 已存在) → 錯誤，不寫入。"""
    resp = client.post("/nvrs/new", data={
        "nvr_id": "NVR-A",  # 重複
        "name": "dup",
        "host": "10.0.0.99",
        "port": "8443",
        "username": "u",
        "password": "p",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "id 重複" in body or "alert-danger" in body


def test_nvr_new_post_invalid_port_rejected(client):
    """POST port=99999 → 錯誤。"""
    resp = client.post("/nvrs/new", data={
        "nvr_id": "BAD-PORT",
        "name": "x",
        "host": "10.0.0.1",
        "port": "99999",
        "username": "u",
        "password": "p",
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Port" in body or "alert-danger" in body


def test_nvr_new_post_missing_password_rejected(client):
    """POST 沒填密碼（new 模式必填）→ 錯誤。"""
    resp = client.post("/nvrs/new", data={
        "nvr_id": "NO-PWD",
        "name": "x",
        "host": "10.0.0.1",
        "port": "8443",
        "username": "u",
        # password 缺
    })
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "密碼" in body or "alert-danger" in body


# === 3. 編輯頁 ===

def test_nvr_edit_get_shows_form_with_password_blank(crud_web_app):
    """GET /nvrs/<id>/edit 200 + 密碼欄位永遠為空。"""
    app, _, nvr_a_id = crud_web_app
    c = app.test_client()
    resp = c.get(f"/nvrs/{nvr_a_id}/edit")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "NVR-A" in body
    assert "A 分店" in body
    # 密碼欄位存在但 value 為空（不顯示舊密碼）
    assert 'name="password"' in body
    # password input 的 value 應為空（不能含 "p"）
    assert 'value="p"' not in body.split('name="password"')[1][:200]


def test_nvr_edit_404_for_missing_id(client):
    """GET /nvrs/9999/edit → 404。"""
    resp = client.get("/nvrs/9999/edit")
    assert resp.status_code == 404


def test_nvr_edit_post_updates_name(crud_web_app):
    """POST 改名 → DB 更新 + 跳轉。"""
    app, _, nvr_a_id = crud_web_app
    c = app.test_client()
    resp = c.post(f"/nvrs/{nvr_a_id}/edit", data={
        "nvr_id": "NVR-A",  # readonly，但仍要帶（Flask test client 不自動填）
        "name": "A 分店（改名後）",
        "host": "10.0.0.1",
        "port": "8443",
        "username": "u",
        # password 留空（password_changed=False）
        "tags": "branch;taipei",
    }, follow_redirects=False)
    assert resp.status_code == 302, f"got {resp.status_code}, body={resp.get_data(as_text=True)[:300]}"

    # 重新查
    resp2 = c.get("/nvrs?q=NVR-A")
    body = resp2.get_data(as_text=True)
    assert "A 分店（改名後）" in body


def test_nvr_edit_password_unchanged_when_empty(crud_web_app):
    """POST 編輯時密碼留空 → DB 內密碼保持原值。"""
    import sqlite3
    app, db_path, nvr_a_id = crud_web_app
    c = app.test_client()

    # 先讀原密碼
    conn = sqlite3.connect(db_path)
    orig_pwd = conn.execute(
        "SELECT password FROM nvr_servers WHERE id = ?", (nvr_a_id,)
    ).fetchone()[0]
    conn.close()
    assert orig_pwd == "p"

    # POST 不填密碼
    c.post(f"/nvrs/{nvr_a_id}/edit", data={
        "nvr_id": "NVR-A",  # readonly，但仍要帶
        "name": "A 分店",
        "host": "10.0.0.1",
        "port": "8443",
        "username": "u",
        # password 留空
    })

    # 確認密碼仍是原值
    conn = sqlite3.connect(db_path)
    new_pwd = conn.execute(
        "SELECT password FROM nvr_servers WHERE id = ?", (nvr_a_id,)
    ).fetchone()[0]
    conn.close()
    assert new_pwd == "p", "密碼留空時不應被覆寫"


def test_nvr_edit_password_changed_when_filled(crud_web_app):
    """POST 編輯時密碼有填 → DB 內密碼更新。"""
    import sqlite3
    app, db_path, nvr_a_id = crud_web_app
    c = app.test_client()

    c.post(f"/nvrs/{nvr_a_id}/edit", data={
        "nvr_id": "NVR-A",  # readonly，但仍要帶
        "name": "A 分店",
        "host": "10.0.0.1",
        "port": "8443",
        "username": "u",
        "password": "new_secret",  # 填新密碼
    })

    conn = sqlite3.connect(db_path)
    new_pwd = conn.execute(
        "SELECT password FROM nvr_servers WHERE id = ?", (nvr_a_id,)
    ).fetchone()[0]
    conn.close()
    assert new_pwd == "new_secret", "填新密碼時應覆寫"


# === 4. 刪除 ===

def test_nvr_delete_post_removes_nvr_and_cameras(crud_web_app):
    """POST 刪除 → NVR + cameras 都消失。"""
    import sqlite3
    app, db_path, nvr_a_id = crud_web_app
    c = app.test_client()

    # 確認刪除前 cameras 存在
    conn = sqlite3.connect(db_path)
    cam_before = conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE nvr_id = ?", (nvr_a_id,)
    ).fetchone()[0]
    conn.close()
    assert cam_before == 2

    resp = c.post(f"/nvrs/{nvr_a_id}/delete", follow_redirects=False)
    assert resp.status_code == 302

    # 確認刪除後 NVR + cameras 都消失
    conn = sqlite3.connect(db_path)
    nvr_after = conn.execute(
        "SELECT COUNT(*) FROM nvr_servers WHERE id = ?", (nvr_a_id,)
    ).fetchone()[0]
    cam_after = conn.execute(
        "SELECT COUNT(*) FROM cameras WHERE nvr_id = ?", (nvr_a_id,)
    ).fetchone()[0]
    # scan_runs / events 仍存在（即使 NVR 沒了，歷史保留）
    events_after = conn.execute(
        "SELECT COUNT(*) FROM events WHERE nvr_id = ?", (nvr_a_id,)
    ).fetchone()[0]
    runs_after = conn.execute(
        "SELECT COUNT(*) FROM scan_runs"
    ).fetchone()[0]
    conn.close()
    assert nvr_after == 0, "NVR 應被刪除"
    assert cam_after == 0, "cameras 應一併刪除"
    assert events_after == 1, "events 應保留（歷史）"
    assert runs_after == 1, "scan_runs 應保留（歷史）"


def test_nvr_delete_404_for_missing_id(client):
    """POST /nvrs/9999/delete → 404 或 flash + redirect。"""
    resp = client.post("/nvrs/9999/delete", follow_redirects=False)
    # 路由沒顯式處理 missing，會 raise ValueError → flash danger → redirect
    assert resp.status_code in (302, 404)