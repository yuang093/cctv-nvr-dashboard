"""Tests for /clips/coverage/data JSON API endpoint (Spec F Task 2)."""
from __future__ import annotations

import os

import pytest

# 設定 env vars **在 import 前**，跟其他 clips 測試一致
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")

from db.sqlite_writer import SqliteWriter  # noqa: E402
from web.clips_app import app  # noqa: E402
from web import db as webdb  # noqa: E402


def _init_db(db_path: str) -> None:
    """用 SqliteWriter 初始化 schema。"""
    w = SqliteWriter(db_path)
    w._init_schema()


@pytest.fixture
def clips_app(monkeypatch, tmp_path):
    """空的 clips app（DB 在 tmp_path，不灌資料）。

    回傳 Flask app 本身（不是 tuple）讓測試可直接 .config[...]/.test_client()。
    """
    db_path = str(tmp_path / "coverage_endpoint_test.db")
    monkeypatch.setenv("NVR_DB_PATH", db_path)
    # coverage endpoint 在 clips_app.py 第一步就檢查 AVIGILON_USER_NONCE/KEY，
    # 缺就回 500，測試就到不了「找不到 NVR → 404 / bad window → 400」那一步。
    # 個別測試若要驗「沒設就 500」必須自己 monkeypatch.delenv（見 _500_when_env_missing）。
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test-nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test-key")
    _init_db(db_path)
    app.config["DB_PATH"] = db_path
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess.clear()
    return app


def test_coverage_data_endpoint_404_when_nvr_missing(clips_app):
    """無此 internal_id → 404。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage/data?nvr_id=99999&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z")
    assert rv.status_code == 404


def test_coverage_data_endpoint_400_on_bad_window(clips_app):
    """end < start → 400。"""
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-T",
        "name": "NVR-T2",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-T2")
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T05:00:00Z&end=2026-08-04T01:00:00Z"
    )
    assert rv.status_code == 400


def test_coverage_data_endpoint_502_when_nvr_unreachable(clips_app):
    """NVR 連線失敗 → 502（包裝為 JSON 錯誤）。"""
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-UNREACH",
        "name": "NVR-UNREACH",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-UNREACH")
    # 至少給 1 台 cam，否則 endpoint 會先回 404「沒有 cam」
    writer = SqliteWriter(db_path)
    writer.begin_scan_run("2026-08-04T00:00:00Z")
    writer.upsert_cameras(nvr_row["id"], {
        "cam-001": {"name": "Cam 1", "available": True, "connection_state": "CONNECTED"},
    })
    writer.finish_scan_run(nvr_row["id"], finished_at="2026-08-04T00:00:30Z", status="success",
                           stats={"total_cameras": 1, "abnormal_cameras": 0,
                                  "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    # 給 env vars 避免 _login_nvr 跑去 console prompt
    os.environ["AVIGILON_USER_NONCE"] = "test-nonce"
    os.environ["AVIGILON_USER_KEY"] = "test-key"
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z"
    )
    # 預期 502（連線失敗）；也接受 200 若實作選擇了 graceful fallback
    assert rv.status_code in (200, 502)
    if rv.status_code == 502:
        assert "error" in rv.get_json()


def test_coverage_data_endpoint_500_when_env_missing(monkeypatch, clips_app):
    """缺 AVIGILON_USER_NONCE/KEY → 500（不允許 fallback 到 console prompt）。"""
    # 清掉 env（fixture 已經 monkeypatch setenv 過；強制再清）
    monkeypatch.delenv("AVIGILON_USER_NONCE", raising=False)
    monkeypatch.delenv("AVIGILON_USER_KEY", raising=False)
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-NOENV",
        "name": "NVR-NOENV",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-NOENV")
    # 至少給 1 台 cam，否則 endpoint 會先回 404「沒有 cam」
    writer = SqliteWriter(db_path)
    writer.begin_scan_run("2026-08-04T00:00:00Z")
    writer.upsert_cameras(nvr_row["id"], {
        "cam-001": {"name": "Cam 1", "available": True, "connection_state": "CONNECTED"},
    })
    writer.finish_scan_run(nvr_row["id"], finished_at="2026-08-04T00:00:30Z", status="success",
                           stats={"total_cameras": 1, "abnormal_cameras": 0,
                                  "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z"
    )
    assert rv.status_code == 500
    body = rv.get_json()
    assert "error" in body
    # 明確告訴 admin 是哪個 env 沒設
    assert "AVIGILON_USER_NONCE" in body["error"]


def test_coverage_data_endpoint_400_on_invalid_iso(clips_app):
    """start 非 ISO 格式 → 400（不是 502）。"""
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-BADISO",
        "name": "NVR-BADISO",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-BADISO")
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=not-a-date&end=2026-08-04T01:00:00Z"
    )
    assert rv.status_code == 400
    body = rv.get_json()
    assert "error" in body
    # 錯誤訊息要引導 client 修正 ISO 格式
    assert "ISO" in body["error"] or "iso" in body["error"].lower()


def test_coverage_page_renders_html(clips_app):
    """GET /clips/coverage → 200 + HTML 含「錄影熱區」。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage")
    assert rv.status_code == 200
    html = rv.get_data(as_text=True)
    assert "錄影熱區" in html


def test_coverage_page_has_nvr_dropdown(clips_app):
    """頁面有 NVR select 元素。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage")
    html = rv.get_data(as_text=True)
    assert 'id="nvr-select"' in html or "id='nvr-select'" in html


def test_coverage_page_has_dark_toggle(clips_app):
    """頁面有 dark toggle 連結到 /dark/toggle。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage")
    html = rv.get_data(as_text=True)
    assert "/dark/toggle" in html


class TestCoverageTrendsDeepLink:
    """Spec G Batch C Task 11：coverage.html (8555) JS dynamic cam rows 加 📈 deep-link。

    8555 跟 8444 跨 port；不能用相對 /trends。
    從 env NVR_DASHBOARD_URL 注入（context_processor）；預設 http://127.0.0.1:8444。
    """

    def test_coverage_page_injects_dashboard_url_globals(self, clips_app):
        """coverage.html 應注入 window.NVR_DASHBOARD_URL（從 env 預設 8444）。"""
        client = clips_app.test_client()
        rv = client.get("/clips/coverage")
        html = rv.get_data(as_text=True)
        assert "window.NVR_DASHBOARD_URL" in html, \
            "coverage 應注入 window.NVR_DASHBOARD_URL 給 JS 用"
        # 預設 URL 是 http://127.0.0.1:8444（context_processor 預設值）
        assert "http://127.0.0.1:8444" in html, \
            "預設 dashboard URL 應是 http://127.0.0.1:8444"
        # 編碼後的 JSON 字串（tojson）會帶引號
        assert '"http://127.0.0.1:8444"' in html or "'http://127.0.0.1:8444'" in html, \
            "dashboard_url 應用 |tojson 序列化（帶引號）"

    def test_coverage_page_uses_env_dashboard_url_override(self, clips_app, monkeypatch):
        """env NVR_DASHBOARD_URL 覆寫 → 注入 HTML。"""
        monkeypatch.setenv("NVR_DASHBOARD_URL", "http://lan.example.com:9000")
        client = clips_app.test_client()
        rv = client.get("/clips/coverage")
        html = rv.get_data(as_text=True)
        # env 設的 URL 應出現在 window.NVR_DASHBOARD_URL 全域賦值
        assert '"http://lan.example.com:9000"' in html or "'http://lan.example.com:9000'" in html, \
            "NVR_DASHBOARD_URL env 應覆寫預設 URL（tojson 後的值）"

    def test_coverage_js_uses_encodeURIComponent_for_cam_id(self, clips_app):
        """JS 內應用 encodeURIComponent(cam.cam_id) 組 deep-link（防 XSS）。

        修法：peer reviewer 風險 2 — 不用相對 /trends，且 URL 參數必須 encodeURIComponent。
        """
        client = clips_app.test_client()
        rv = client.get("/clips/coverage")
        html = rv.get_data(as_text=True)
        # JS 內含 encodeURIComponent(cam.cam_id) 模式（取自 cam_iteration）
        assert "encodeURIComponent(cam.cam_id)" in html, \
            "coverage.html JS 應用 encodeURIComponent 包 cam_id"
        # deep-link 模式：'/trends?cam_id=' + encodeURIComponent(...)
        assert "/trends?cam_id=" in html, \
            "JS 應組 /trends?cam_id= 路徑（用 dashboard URL 當 base）"
        # 不應用相對路徑直接組（peer reviewer 風險 2）— 檢查 window 全域被讀取
        assert "window.NVR_DASHBOARD_URL" in html, \
            "JS 應用 window.NVR_DASHBOARD_URL 當 base（不是相對路徑）"
        # 確認 deep-link href 內組合：dashboard URL + /trends?cam_id=
        assert "trendLink.href" in html, \
            "JS 應設 trendLink.href 給 deep-link anchor"

    def test_coverage_js_has_cam_trend_link_class(self, clips_app):
        """JS 內應建 .cam-trend-link class 的 anchor（給 event delegation 識別）。"""
        client = clips_app.test_client()
        rv = client.get("/clips/coverage")
        html = rv.get_data(as_text=True)
        assert "'cam-trend-link'" in html or '"cam-trend-link"' in html, \
            "JS 應建 class='cam-trend-link' anchor 給 deep-link 用"


def test_coverage_data_endpoint_502_when_nvr_unreachable_strict(monkeypatch, clips_app):
    """嚴格 502：monkeypatch 讓 scanner 連線失敗（spec 要求 502 而非 200 fallback）。"""
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-STRICT",
        "name": "NVR-STRICT",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-STRICT")
    # 給 cam
    writer = SqliteWriter(db_path)
    writer.begin_scan_run("2026-08-04T00:00:00Z")
    writer.upsert_cameras(nvr_row["id"], {
        "cam-001": {"name": "Cam 1", "available": True, "connection_state": "CONNECTED"},
    })
    writer.finish_scan_run(nvr_row["id"], finished_at="2026-08-04T00:00:30Z", status="success",
                           stats={"total_cameras": 1, "abnormal_cameras": 0,
                                  "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    # env 已 set（fixture 之上 NVR_CLIPS_CLIENT=mock；但這條測試想測的是
    # "即便 env 都對、scanner 連線也失敗"→ 502；所以不走 _login_nvr 整段）
    os.environ["AVIGILON_USER_NONCE"] = "test-nonce"
    os.environ["AVIGILON_USER_KEY"] = "test-key"
    # 直接 monkeypatch _login_nvr 讓它 raise，模擬 NVR 連線失敗
    from web import clips_app as clips_app_mod
    def _boom(*a, **kw):
        raise RuntimeError("NVR unreachable: Connection refused")
    monkeypatch.setattr(clips_app_mod, "_login_nvr", _boom)
    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z"
    )
    # 嚴格要求 502（不再接受 200 fallback）
    assert rv.status_code == 502
    body = rv.get_json()
    assert "error" in body


def test_coverage_data_endpoint_with_mock_timeline(clips_app, monkeypatch):
    """整合：mock AvigilonScanner.get_timeline，回 1 台 cam 1 段錄影、預期完整率 1.0。

    Spec F Task 4：模擬 happy path — 1 台 NVR + 1 台 cam + 1 段完整時窗錄影。
    """
    webdb.create_nvr(clips_app.config["DB_PATH"], {
        "nvr_id": "ACC8-MOCK",
        "name": "NVR-MOCK",
        "host": "10.0.0.99",
        "port": 8443,
        "username": "u",
        "password": "p",
        "verify_ssl": False,
    })
    db_path = clips_app.config["DB_PATH"]
    nvr_row = next(n for n in webdb.get_nvrs(db_path) if n["name"] == "NVR-MOCK")
    # 給 1 台 cam
    writer = SqliteWriter(db_path)
    writer.begin_scan_run("2026-08-04T00:00:00Z")
    writer.upsert_cameras(nvr_row["id"], {
        "cam-mock-1": {"name": "MockCam1", "available": True, "connection_state": "CONNECTED"},
    })
    writer.finish_scan_run(nvr_row["id"], finished_at="2026-08-04T00:00:30Z", status="success",
                           stats={"total_cameras": 1, "abnormal_cameras": 0,
                                  "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})

    # env 給齊，避免 endpoint 提早 500
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test-nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test-key")

    # 1. stub _login_nvr：給假 token，不打真 NVR
    from web import clips_app as clips_app_mod
    monkeypatch.setattr(clips_app_mod, "_login_nvr", lambda nvr_row: "FAKE-TOKEN")

    # 2. stub AvigilonScanner.get_timeline：回傳「覆蓋整個視窗」的錄影
    from nvr_scanner import AvigilonScanner

    def fake_get_timeline(self, cam_id, from_iso=None, to_iso=None, **kwargs):
        return {
            "result": {
                "timelines": [
                    {
                        "cameraId": cam_id,
                        "record": [
                            {"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"},
                        ],
                    }
                ]
            }
        }

    monkeypatch.setattr(AvigilonScanner, "get_timeline", fake_get_timeline)

    client = clips_app.test_client()
    rv = client.get(
        f"/clips/coverage/data?nvr_id={nvr_row['id']}"
        f"&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z"
    )
    assert rv.status_code == 200, f"unexpected status: {rv.status_code} body={rv.get_data(as_text=True)[:300]}"
    data = rv.get_json()
    assert data["nvr_id"] == "ACC8-MOCK"
    assert len(data["cameras"]) == 1
    cam1 = data["cameras"][0]
    assert cam1["cam_id"] == "cam-mock-1"
    assert cam1["camera_name"] == "MockCam1"
    assert cam1["completeness"] == 1.0
    assert len(cam1["records"]) == 1
    assert cam1["records"][0][0] == "2026-08-04T00:00:00+00:00"
    assert cam1["records"][0][1] == "2026-08-04T01:00:00+00:00"
