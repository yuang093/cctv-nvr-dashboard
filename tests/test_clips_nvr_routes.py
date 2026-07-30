"""
tests/test_clips_nvr_routes.py
==============================
Phase 2.7 補：8555 clips app 上的 NVR CRUD Blueprint + Dark Mode 測試。

策略：用 Flask test_client + seeded DB（與 test_clips_app.py 同樣 fixture pattern）。
確保：
- NVR CRUD 全部跑得通（list/new/edit/delete/toggle/import/export/template）
- test-connection 缺 env 時正確回錯誤
- Dark mode toggle 切 session，且 dark 變數注入 clips 跟 nvrs 兩個頁面
- Clips 頁 navbar 沒 NVR 清單連結、NVR 頁 navbar 沒 Clips 連結（獨立 UI）
"""
from __future__ import annotations

import io
import os

import pytest

# 設定 env vars **在 import 前**
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")


from db.sqlite_writer import SqliteWriter  # noqa: E402
from web.clips_app import app  # noqa: E402
from web import db as webdb  # noqa: E402


def _init_db(db_path: str) -> None:
    """用 SqliteWriter 初始化 schema（建立 nvr_servers / cameras 等表）。"""
    w = SqliteWriter(db_path)
    w._init_schema()


# === Fixtures ===

@pytest.fixture
def clips_app(monkeypatch, tmp_path):
    """空的 clips app（DB 在 tmp_path，不灌資料）。"""
    db_path = str(tmp_path / "nvr_routes_test.db")
    monkeypatch.setenv("NVR_DB_PATH", db_path)
    _init_db(db_path)
    # 重設 DB_PATH config 跟 session store
    app.config["DB_PATH"] = db_path
    app.config["TESTING"] = True
    # 清掉 session（避免跨測試污染 dark mode state）
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess.clear()
    yield app, db_path


@pytest.fixture
def clips_app_with_nvr(monkeypatch, tmp_path):
    """灌一台 NVR 的 clips app。"""
    db_path = str(tmp_path / "nvr_routes_test_seeded.db")
    monkeypatch.setenv("NVR_DB_PATH", db_path)
    _init_db(db_path)
    app.config["DB_PATH"] = db_path
    app.config["TESTING"] = True

    webdb.create_nvr(db_path, {
        "nvr_id": "TEST-NVR-1",
        "name": "測試 NVR",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "admin",
        "password": "secret",
        "verify_ssl": False,
        "site_id": "LAB",
        "tags": ["test", "lab"],
    })

    yield app, db_path


# === 路由存在性 ===

def test_clips_app_has_nvr_routes(clips_app):
    """Blueprint 註冊後，預期的 11 條 NVR routes 都應該存在。"""
    app_, _ = clips_app
    rules = {r.rule for r in app_.url_map.iter_rules()}
    expected = {
        "/nvrs/",
        "/nvrs/new",
        "/nvrs/import",
        "/nvrs/import/template.csv",
        "/nvrs/import/template.json",
        "/nvrs/export.csv",
        "/nvrs/export.json",
        "/nvrs/test-connection",
        # /nvrs/<id>/toggle、/edit、/delete 用 <int:>，檢查 pattern 存在
    }
    assert expected.issubset(rules), f"缺的 routes：{expected - rules}"
    # 動態 routes
    assert any("toggle" in r.rule for r in app_.url_map.iter_rules())
    assert any("edit" in r.rule for r in app_.url_map.iter_rules())
    assert any("delete" in r.rule for r in app_.url_map.iter_rules())


def test_clips_app_has_dark_toggle_route(clips_app):
    app_, _ = clips_app
    rules = [r for r in app_.url_map.iter_rules() if r.rule == "/dark/toggle"]
    assert len(rules) == 1
    assert "POST" in rules[0].methods


# === NVR 清單 ===

def test_nvrs_list_renders_empty(clips_app):
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "NVR 清單" in body
        assert "尚無 NVR 紀錄" in body


def test_nvrs_list_renders_one(clips_app_with_nvr):
    app_, _ = clips_app_with_nvr
    with app_.test_client() as c:
        r = c.get("/nvrs/")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "TEST-NVR-1" in body
        assert "測試 NVR" in body
        assert "10.0.0.1" in body


def test_nvrs_list_search_filter(clips_app_with_nvr):
    app_, _ = clips_app_with_nvr
    with app_.test_client() as c:
        r = c.get("/nvrs/?q=TEST")
        assert r.status_code == 200
        assert "TEST-NVR-1" in r.data.decode("utf-8")
        # 搜尋無結果
        r = c.get("/nvrs/?q=不存在")
        assert "無符合的 NVR" in r.data.decode("utf-8")


def test_nvrs_list_pagination(clips_app):
    """page 參數超出範圍不應該 500。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/?page=999")
        assert r.status_code == 200


# === 新增 / 編輯 / 刪除 ===

def test_nvrs_new_get_form(clips_app):
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/new")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "新增 NVR" in body
        assert 'name="nvr_id"' in body
        assert 'name="host"' in body


def test_nvrs_new_post_creates(clips_app, tmp_path):
    app_, db_path = clips_app
    with app_.test_client() as c:
        r = c.post("/nvrs/new", data={
            "nvr_id": "NEW-NVR",
            "name": "新 NVR",
            "host": "192.168.1.100",
            "port": "8443",
            "username": "admin",
            "password": "pw",
            "verify_ssl": "",
            "site_id": "",
            "tags": "",
        }, follow_redirects=True)
        assert r.status_code == 200
        assert "已建立 NVR" in r.data.decode("utf-8")
        # DB 內真的有這筆
        nvrs = webdb.get_nvrs(db_path)
        assert any(n["nvr_id"] == "NEW-NVR" for n in nvrs)


def test_nvrs_new_post_validation_error(clips_app):
    """缺密碼時應該留在 form 頁並顯示錯誤，不應該寫入 DB。"""
    app_, db_path = clips_app
    with app_.test_client() as c:
        r = c.post("/nvrs/new", data={
            "nvr_id": "BAD-NVR",
            "name": "壞 NVR",
            "host": "x",
            "port": "8443",
            "username": "u",
            "password": "",  # 缺密碼
        })
        assert r.status_code == 200
        assert "密碼必填" in r.data.decode("utf-8")
        # DB 內沒有這筆
        nvrs = webdb.get_nvrs(db_path)
        assert not any(n["nvr_id"] == "BAD-NVR" for n in nvrs)


def test_nvrs_edit_get_form(clips_app_with_nvr):
    app_, _ = clips_app_with_nvr
    with app_.test_client() as c:
        nvr = webdb.get_nvrs(webdb._NVR_LIST_DEFAULT_DB_PATH if hasattr(webdb, '_NVR_LIST_DEFAULT_DB_PATH') else "")[0] if False else webdb.get_nvrs(app_.config["DB_PATH"])[0]
        r = c.get(f"/nvrs/{nvr['id']}/edit")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "編輯 NVR" in body
        # 密碼欄位永遠為空（UI 不顯示舊密碼）
        assert 'value=""' in body or "placeholder" in body
        # nvr_id 欄位 readonly
        assert "readonly" in body


def test_nvrs_edit_post_updates(clips_app_with_nvr):
    app_, db_path = clips_app_with_nvr
    with app_.test_client() as c:
        nvr = webdb.get_nvrs(db_path)[0]
        r = c.post(f"/nvrs/{nvr['id']}/edit", data={
            "nvr_id": nvr["nvr_id"],
            "name": "改過的名字",
            "host": "10.0.0.99",
            "port": "8443",
            "username": "admin",
            "password": "",  # 留空 = 不改密碼
            "site_id": "LAB2",
            "tags": "updated",
        }, follow_redirects=True)
        assert r.status_code == 200
        assert "已更新 NVR" in r.data.decode("utf-8")
        updated = webdb.get_nvr(db_path, nvr["id"])
        assert updated["name"] == "改過的名字"
        assert updated["host"] == "10.0.0.99"
        # 密碼應該保留原值（沒被清空）
        assert updated["password"] == "secret"


def test_nvrs_delete_post_removes(clips_app_with_nvr):
    app_, db_path = clips_app_with_nvr
    with app_.test_client() as c:
        nvr = webdb.get_nvrs(db_path)[0]
        r = c.post(f"/nvrs/{nvr['id']}/delete", follow_redirects=True)
        assert r.status_code == 200
        assert "已刪除 NVR" in r.data.decode("utf-8")
        assert webdb.get_nvr(db_path, nvr["id"]) is None


def test_nvrs_toggle_enabled(clips_app_with_nvr):
    app_, db_path = clips_app_with_nvr
    with app_.test_client() as c:
        nvr = webdb.get_nvrs(db_path)[0]
        assert nvr["enabled"] == 1
        r = c.post(f"/nvrs/{nvr['id']}/toggle", follow_redirects=True)
        assert r.status_code == 200
        assert "已停用" in r.data.decode("utf-8")
        toggled = webdb.get_nvr(db_path, nvr["id"])
        assert toggled["enabled"] == 0
        # 再 toggle 一次 → 啟用
        r = c.post(f"/nvrs/{nvr['id']}/toggle", follow_redirects=True)
        assert "已啟用" in r.data.decode("utf-8")


# === 匯入 / 匯出 ===

def test_nvrs_import_get_form(clips_app):
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/import")
        assert r.status_code == 200
        assert "批次匯入" in r.data.decode("utf-8")


def test_nvrs_import_post_csv(clips_app, tmp_path):
    app_, db_path = clips_app
    csv_content = (
        "id,name,host,port,username,password,verify_ssl,site_id,tags\r\n"
        "IMP-1,匯入A,10.1.1.1,8443,admin,pw,0,SITE,branch;taipei\r\n"
        "IMP-2,匯入B,10.1.1.2,8443,admin,pw,0,,branch;taichung\r\n"
    )
    with app_.test_client() as c:
        r = c.post("/nvrs/import", data={
            "file": (io.BytesIO(csv_content.encode("utf-8-sig")), "test.csv"),
        }, content_type="multipart/form-data", follow_redirects=True)
        assert r.status_code == 200
        assert "匯入完成" in r.data.decode("utf-8")
        nvrs = webdb.get_nvrs(db_path)
        ids = {n["nvr_id"] for n in nvrs}
        assert "IMP-1" in ids
        assert "IMP-2" in ids


def test_nvrs_import_template_csv(clips_app):
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/import/template.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["Content-Type"]
        body = r.data.decode("utf-8-sig")
        assert "id,name,host,port" in body
        assert "ACC8-P4" in body


def test_nvrs_import_template_json(clips_app):
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/import/template.json")
        assert r.status_code == 200
        import json as _json
        data = _json.loads(r.data.decode("utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 1
        assert "id" in data[0]


def test_nvrs_export_csv(clips_app_with_nvr):
    app_, _ = clips_app_with_nvr
    with app_.test_client() as c:
        r = c.get("/nvrs/export.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["Content-Type"]
        body = r.data.decode("utf-8-sig")
        assert "TEST-NVR-1" in body
        assert "10.0.0.1" in body


def test_nvrs_export_json(clips_app_with_nvr):
    app_, _ = clips_app_with_nvr
    with app_.test_client() as c:
        r = c.get("/nvrs/export.json")
        assert r.status_code == 200
        import json as _json
        data = _json.loads(r.data.decode("utf-8"))
        assert isinstance(data, list)
        # export 用 `id` 而非 `nvr_id`（跟 import 範本對齊）
        assert any(d["id"] == "TEST-NVR-1" for d in data)


# === 測試連線 ===

def test_nvrs_test_connection_missing_env(clips_app, monkeypatch):
    """沒 AVIGILON_USER_NONCE/KEY 時，回 500 + 錯誤訊息。"""
    app_, _ = clips_app
    monkeypatch.delenv("AVIGILON_USER_NONCE", raising=False)
    monkeypatch.delenv("AVIGILON_USER_KEY", raising=False)
    with app_.test_client() as c:
        r = c.post("/nvrs/test-connection", json={
            "host": "10.0.0.1", "port": 8443,
            "username": "u", "password": "p",
            "verify_ssl": False,
        })
        assert r.status_code == 500
        data = r.get_json()
        assert data["ok"] is False
        assert "AVIGILON_USER_NONCE" in data["message"]


def test_nvrs_test_connection_missing_field(clips_app):
    """缺欄位時回 400。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.post("/nvrs/test-connection", json={
            "host": "10.0.0.1",
            # 缺 port / username / password
        })
        assert r.status_code == 400
        assert r.get_json()["ok"] is False


# === Dark Mode ===

def test_dark_toggle_flips_session(clips_app):
    """POST /dark/toggle 應該翻轉 session['dark']。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        # 初始 False
        with c.session_transaction() as sess:
            assert sess.get("dark", False) is False
        # toggle 一次
        c.post("/dark/toggle")
        with c.session_transaction() as sess:
            assert sess.get("dark", False) is True
        # 再 toggle 一次
        c.post("/dark/toggle")
        with c.session_transaction() as sess:
            assert sess.get("dark", False) is False


def test_dark_context_injected_in_clips(clips_app):
    """GET /clips 應該有 dark 變數（給 toggle button 用）。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/clips")
        body = r.data.decode("utf-8")
        # 預設 light，toggle 顯示 🌙
        assert "🌙" in body
        assert "dark/toggle" in body


def test_dark_context_injected_in_nvrs(clips_app):
    """GET /nvrs 應該有 dark 變數。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/")
        body = r.data.decode("utf-8")
        assert "🌙" in body
        assert "dark/toggle" in body


def test_dark_css_renders_when_dark_true(clips_app):
    """session dark=True 後，clips.html 應該 render dark CSS block。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        with c.session_transaction() as sess:
            sess["dark"] = True
        r = c.get("/clips")
        body = r.data.decode("utf-8")
        # dark CSS block 存在（背景色 #0c1220）
        assert "#0c1220" in body
        # toggle 顯示 ☀️（sun）
        assert "☀️" in body


def test_dark_css_renders_when_dark_true_nvrs(clips_app):
    app_, _ = clips_app
    with app_.test_client() as c:
        with c.session_transaction() as sess:
            sess["dark"] = True
        r = c.get("/nvrs/")
        body = r.data.decode("utf-8")
        assert "#0c1220" in body
        assert "☀️" in body


# === 跨頁 cross-link ===

def test_clips_page_has_nvr_nav_link(clips_app):
    """Clips 頁 navbar 要有「NVR 清單」連結（2026-07-09 user 要求：方便編輯 NVR 不用輸入網址）。

    方向：clips → NVR 清單（單向）。
    NVR 清單頁 navbar 仍不連到 clips（保持各自獨立的入口感）。
    """
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/clips")
        body = r.data.decode("utf-8")
        # 渲染後的 URL（flask 解析 url_for → /nvrs/）
        assert 'href="/nvrs/"' in body
        # 視覺文字
        assert "NVR 清單" in body


def test_clips_template_has_nvr_nav_link_raw():
    """回歸測試：clips.html 模板裡 raw string 要有 url_for('nvr.list')。"""
    body = Path_filesafe_read("web/clips_templates/clips.html")
    assert "{{ url_for('nvr.list') }}" in body
    assert "NVR 清單" in body


def test_nvrs_page_has_no_clips_nav_link(clips_app):
    """NVR 頁 navbar 不應該有「Clips」連結（保持兩功能各自的入口）。"""
    app_, _ = clips_app
    with app_.test_client() as c:
        r = c.get("/nvrs/")
        body = r.data.decode("utf-8")
        assert 'href="/clips' not in body
        assert "url_for('clips" not in body  # 沒漏寫 url_for


# === Template 結構回歸測試（防止以後改壞） ===

def test_clips_template_has_dark_toggle_button():
    """clips.html navbar 必須有 dark toggle form。"""
    body = Path_filesafe_read("web/clips_templates/clips.html")
    assert "{{ url_for('dark_toggle') }}" in body
    assert "🌙" in body or "☀️" in body


def test_nvrs_list_template_independent():
    """nvrs_list.html 不應該 extends base.html（獨立 UI）。"""
    body = Path_filesafe_read("web/clips_templates/nvrs_list.html")
    assert "{% extends" not in body
    assert "影片片段調閱" not in body  # 沒有 clips 功能名稱


def test_nvrs_form_template_independent():
    body = Path_filesafe_read("web/clips_templates/nvr_form.html")
    assert "{% extends" not in body


def test_nvrs_import_template_independent():
    body = Path_filesafe_read("web/clips_templates/nvr_import.html")
    assert "{% extends" not in body


def test_nvrs_list_template_has_dark_toggle():
    body = Path_filesafe_read("web/clips_templates/nvrs_list.html")
    assert "{{ url_for('dark_toggle') }}" in body


@pytest.mark.parametrize("template_file", [
    "web/clips_templates/nvrs_list.html",
    "web/clips_templates/nvr_form.html",
    "web/clips_templates/nvr_import.html",
])
def test_nvr_templates_have_dark_toggle(template_file):
    """3 個 NVR CRUD template 都要有 dark mode toggle form。"""
    body = Path_filesafe_read(template_file)
    assert "{{ url_for('dark_toggle') }}" in body


# === Helper ===

def Path_filesafe_read(path):
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")