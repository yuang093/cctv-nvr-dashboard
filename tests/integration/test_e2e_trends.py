"""web/app.py /trends route 整合測試（Flask test_client + 真 SQLite）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from web.app import create_app


@pytest.fixture
def flask_client(tmp_path: Path, monkeypatch):
    """8444 app + tmp DB。"""
    db_path = str(tmp_path / "trends.db")

    # 建最小 schema（含 spec G 需要的 3 張表 + 完整 nvr_servers 欄位以相容 list_enabled_nvrs）
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            host TEXT NOT NULL DEFAULT '',
            port INTEGER NOT NULL DEFAULT 8443,
            username TEXT,
            password TEXT,
            verify_ssl INTEGER NOT NULL DEFAULT 0,
            site_id TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            is_ghost INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE image_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            nvr_server_id INTEGER,
            checked_at_utc TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            flags_json TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    # 用 factory 建 app 並注入 db_path（與既有 test_e2e_web.py 風格一致）
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, db_path


class TestTrendsRoute:
    def test_route_returns_200(self, flask_client):
        """GET /trends → 200。"""
        client, _ = flask_client
        r = client.get("/trends")
        assert r.status_code == 200

    def test_default_range_is_24h(self, flask_client):
        """不帶 query → 24h 模式（page 含「Cam 健康趨勢」標題）。"""
        client, db_path = flask_client
        # seed 1 台 NVR + 1 cam
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'd-1', 'Cam1', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()

        r = client.get("/trends")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "Cam 健康趨勢" in body

    def test_invalid_range_falls_back_to_24h(self, flask_client):
        """?range=99 → 200（fallback 24h，不 500）。"""
        client, _ = flask_client
        r = client.get("/trends?range=99")
        assert r.status_code == 200

    def test_nvr_dropdown_lists_all_nvrs(self, flask_client):
        """template 內含所有 NVR 名（給 dropdown）。"""
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-b', 'ACC-9')")
        conn.commit()
        conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        assert "ACC-8" in body
        assert "ACC-9" in body


class TestTrendsTemplateRendering:
    """驗證 trends.html 結構（spec §4.3 / §7.2）。"""

    def _seed_one_cam_with_health(self, db_path: str, cam_id: str = "d-1") -> None:
        """塞 1 NVR + 1 cam + 1 筆 record。"""
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, 'Cam1', 0, '2026-08-05T00:00:00Z')",
            (nvr_int, cam_id),
        )
        conn.execute(
            "INSERT INTO image_health_checks "
            "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
            "VALUES (?, ?, '2026-08-05T12:00:00Z', ?, '[]')",
            (cam_id, nvr_int, json.dumps({"is_frozen": False, "is_underexposed": False})),
        )
        conn.commit()
        conn.close()

    def test_with_seeded_data_shows_bins(self, flask_client):
        """seed image_health → 模板含 chart canvas。"""
        client, db_path = flask_client
        self._seed_one_cam_with_health(db_path)

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        assert "<canvas" in body, "應含 Chart.js canvas"
        # Chart.js CDN script（沿用 fleet.html 的 4.4.0）
        assert "chart.js" in body.lower() or "Chart.js" in body
        assert "Cam1" in body, "應顯示 cam 名稱"

    def test_abnormal_badge_for_3plus_abnormal_bins(self, flask_client):
        """abnormal_bins >= 1 的 cam 顯示 cam-card 紅色邊框。"""
        client, db_path = flask_client
        self._seed_one_cam_with_health(db_path, "d-bad")
        # seed 3 筆 frozen → 3 bin abnormal
        for h in (4.0, 5.0, 6.0):
            checked = (datetime.now(timezone.utc) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO image_health_checks "
                "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
                "VALUES ('d-bad', NULL, ?, ?, '[]')",
                (checked, json.dumps({"is_frozen": True, "is_underexposed": False})),
            )
            conn.commit()
            conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 紅色框線 badge（class 標記）—— 用 regex 找 class 包含 abnormal 的元素
        import re
        pattern = re.compile(r'class\s*=\s*["\'][^"\']*\babnormal\b', re.IGNORECASE)
        assert pattern.search(body), \
            "異常 cam 應有 cam-card.abnormal CSS class"

    def test_empty_db_shows_empty_state(self, flask_client):
        """空 DB → 顯示「目前沒有 cam 紀錄」相關字串。"""
        client, _ = flask_client
        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 強化：明確找 "目前沒有" 中文字串
        assert "目前沒有" in body, \
            f"空 DB 應顯示「目前沒有」字串，got body length {len(body)}"


class TestTrendsRouteFilters:
    """進階 query param 行為：range=7d、status=abnormal_only。"""

    def test_range_7d_query(self, flask_client):
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.commit()
        conn.close()
        r = client.get("/trends?range=7d")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # 7d 按鈕應 active（class 標記 + 「168h 視窗」顯示）
        assert "168" in body, "?range=7d 應顯示 168h 視窗"

    def test_status_filter_abnormal_only(self, flask_client):
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'd-healthy', 'HealthyCam', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()
        r = client.get("/trends?status=abnormal_only")
        assert r.status_code == 200
        # 沒 abnormal → 看不到 HealthyCam（abnormal_only 過濾）
        body = r.data.decode("utf-8")
        assert "HealthyCam" not in body, \
            "abnormal_only 應過濾掉 0 異常的 cam"

    def test_invalid_status_filter_falls_back_to_any(self, flask_client):
        """無效 status_filter 不應 500 — peer reviewer 2026-08-05 修法：route 寬鬆 normalization。"""
        client, _ = flask_client
        r = client.get("/trends?status=garbage")
        assert r.status_code == 200, \
            "?status=garbage 應 fallback 200（route normalization），不 500"


class TestTrendsTemplateSecurity:
    """Peer reviewer 2026-08-05 提出的 Critical/Important 修法回歸測試。"""

    def test_xss_safe_in_filter_url_construction(self, flask_client):
        """?nvr_id=';alert(1);// 應被 URL-encoded，不應作為 inline JS 字串渲染。

        修法：data-* 屬性 + JS event listener（不靠 inline onchange 字串拼接）。
        任何 href URL 內的 current_nvr 都應 URL-encoded。
        """
        client, db_path = flask_client
        # 先 seed 1 NVR（讓 select 才會渲染）
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.commit()
        conn.close()

        # '、;、a、l、e、r、t 都會被 |urlencode 編碼
        r = client.get("/trends?nvr_id=';alert(1);//")
        assert r.status_code == 200
        body = r.data.decode("utf-8")

        # 不可在 inline onchange 內看到未編碼的 nvr_id payload
        assert "';alert(1)" not in body, \
            "Critical: inline JS 字串拼接 XSS regression — payload 未 URL 編碼"
        # Select 元素的 data-current-nvr 應 URL 編碼（safe）
        assert "data-current-nvr=" in body, "select 應用 data-current-* 屬性模式"
        # %27 是 ' 的 URL 編碼；應在 data attribute 內出現
        assert "%27" in body or "&#x27;" in body or "&apos;" in body, \
            "data attribute 內 nvr_id 應 URL 編碼"

    def test_canvas_id_uses_composite_nvr_cam(self, flask_client):
        """Canvas id 應是 chart-{nvr_id}-{cam_id}（DB UNIQUE 是 (nvr_id, device_id)）。

        修法：peer reviewer 指出只用 cam_id 跨 NVR 會撞 id。
        """
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        nvr_a = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        nvr_b = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-b', 'ACC-9')"
        ).lastrowid
        # 兩台 cam 用相同 device_id "shared"（模擬跨 NVR 同 device_id）
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'shared', 'CamOnA', 0, '2026-08-05T00:00:00Z')",
            (nvr_a,),
        )
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'shared', 'CamOnB', 0, '2026-08-05T00:00:00Z')",
            (nvr_b,),
        )
        conn.commit()
        conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 兩台 cam 各自的 canvas id 應不同：chart-nvr-a-shared vs chart-nvr-b-shared
        assert 'id="chart-nvr-a-shared"' in body, "CamOnA 應有 nvr-prefixed canvas id"
        assert 'id="chart-nvr-b-shared"' in body, "CamOnB 應有 nvr-prefixed canvas id"
        # 不應該只有 chart-shared（會撞 id）
        assert 'id="chart-shared"' not in body, \
            "不能只用 cam_id 沒 nvr_id prefix（peer reviewer 修法回歸）"

    def test_data_attributes_carry_filter_state_for_js(self, flask_client):
        """select / checkbox 應用 data-* 屬性把當前 filter 狀態交給 JS（避免 inline JS）。"""
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.commit()
        conn.close()

        r = client.get("/trends?nvr_id=nvr-a&status=abnormal_only&range=7d")
        body = r.data.decode("utf-8")
        # select 有 id + data-* 屬性
        assert 'id="nvr-filter-select"' in body
        assert 'data-current-nvr="nvr-a"' in body
        assert 'data-current-status="abnormal_only"' in body
        assert 'data-current-range="7d"' in body
        # toggle 有 id，給 JS event listener 綁
        assert 'id="abnormal-only-toggle"' in body
        assert "checked" in body, "?status=abnormal_only 應 pre-check"

    def test_no_inline_onchange_or_onclick_attributes(self, flask_client):
        """Filter / click handlers 應全走 JS addEventListener，不能有 inline on* 屬性。

        修法：peer reviewer Critical XSS。Template 沒 inline onchange/onclick。
        """
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.commit()
        conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 不能有 inline JS 屬性（除了安全範例如 onclick="#" 等；本 template 全不該有）
        assert 'onchange="' not in body, "filter UI 不應用 inline onchange 字串拼接"
        assert 'onclick=' not in body, \
            "cam card 不應用 inline onclick；改用 addEventListener"

    def test_css_var_references_for_theme_awareness(self, flask_client):
        """inline style block 應用 design tokens（var(--bg-card) 等），不寫死 hex。"""
        client, _ = flask_client
        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 至少 5 個 var() 引用
        css_block_count = body.count("var(--")
        assert css_block_count >= 5, \
            f"應用 CSS var 做 theme 自動套色，got {css_block_count} refs（peer reviewer 修法）"
