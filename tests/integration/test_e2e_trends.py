"""web/app.py /trends route 整合測試（Flask test_client + 真 SQLite）。"""
from __future__ import annotations

import json
import re
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
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT '2026-08-05T00:00:00Z'
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


class TestDevicesListTrendsLink:
    """Spec G Batch C Task 9：devices_list.html 每台 cam row 應有 📈 連結到 /trends?cam_id=X。"""

    def test_devices_list_has_trends_link_per_cam(self, flask_client):
        """devices_list 每台 cam 都應有 /trends?cam_id=<device_id> 連結。"""
        client, db_path = flask_client
        # seed 2 台 cam
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'cam-A', 'CamA', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'cam-B', 'CamB', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()

        r = client.get("/devices")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # 兩台 cam 都應有 /trends?cam_id=cam-X 連結（含 range=24h）
        assert re.search(r'href="/trends\?cam_id=cam-A[^"]*range=24h', body), \
            "cam-A 應有 /trends?cam_id=...&range=24h deep link"
        assert re.search(r'href="/trends\?cam_id=cam-B[^"]*range=24h', body), \
            "cam-B 應有 /trends?cam_id=...&range=24h deep link"
        # 📈 emoji 應出現
        assert "📈" in body, "deep-link 應用 📈 icon"

    def test_devices_list_link_is_xss_safe_for_special_device_id(self, flask_client):
        """device_id 含特殊字元 → |urlencode 過濾後注入（防 XSS）。"""
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        # 含 '、"、< 的 device_id
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, 'XssCam', 0, '2026-08-05T00:00:00Z')",
            (nvr_int, "evil'id\"><script>alert(1)</script>"),
        )
        conn.commit()
        conn.close()

        r = client.get("/devices")
        body = r.data.decode("utf-8")
        # 原始 raw payload 不應出現在 href attribute 內
        assert 'href="/trends?cam_id=evil\'id\"' not in body, \
            "Critical: device_id 未 URL-encoded 直接拼接 href → XSS"
        # 編碼後的版本應在 href 內
        assert "cam_id=evil" in body, "deep-link 應被 render（即使 device_id 含特殊字元）"
    """Spec G Batch C Task 8：base.html navbar 應含 '📈 健康趨勢' 連結到 /trends。

    /trends extends base.html，所以 navbar 必渲染。
    """

    def test_navbar_has_trends_link_to_trends_route(self, flask_client):
        """navbar 應含指向 /trends 的 anchor（text 含 '健康趨勢'）。"""
        client, _ = flask_client
        r = client.get("/trends")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # url_for('trends') 會被 render 成 /trends
        assert 'href="/trends"' in body, "navbar 應有指向 /trends 的 anchor"
        # 顯示文字含「健康趨勢」
        assert "健康趨勢" in body, "navbar 應顯示「健康趨勢」文字"
    """Spec G Batch C Task 12：route 接受 ?cam_id= query param，template JS auto-expand + scrollIntoView。

    修法：deep link 從 devices/dashboard/coverage 點進來時，要直接 focus 到該 cam。
    用 JS 端 find `.cam-card[data-cam-id="..."]` 自動展開 detail chart + scroll。
    """

    def test_cam_id_query_param_returns_200(self, flask_client):
        client, db_path = flask_client
        # seed 1 cam
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

        r = client.get("/trends?cam_id=d-1")
        assert r.status_code == 200

    def test_cam_id_marked_for_focus_in_html(self, flask_client):
        """?cam_id=d-1 → HTML 內含 data-cam-id="d-1" 標記，給 JS 找目標。

        修法：route 把 cam_id 傳到 template，template 在 data-focus-cam-id 屬性暴露。
        """
        client, db_path = flask_client
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

        r = client.get("/trends?cam_id=d-1")
        body = r.data.decode("utf-8")
        # HTML 內有 data-cam-id="d-1" 標記
        assert 'data-cam-id="d-1"' in body
        # route 把 cam_id 注入到 root container（給 JS 讀）
        assert "data-focus-cam-id" in body
        assert 'data-focus-cam-id="d-1"' in body

    def test_unknown_cam_id_returns_200_gracefully(self, flask_client):
        """不存在的 cam_id → 仍 200，只是 JS 找不到對應 card（不 500）。"""
        client, _ = flask_client
        r = client.get("/trends?cam_id=does-not-exist")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # root 有 data-focus-cam-id 但不會找到任何 card（no crash）
        assert 'data-focus-cam-id="does-not-exist"' in body

    def test_cam_id_with_special_chars_is_url_encoded(self, flask_client):
        """cam_id 含特殊字元 → URL-encoded 後注入 HTML attribute（防 XSS）。

        修法：用 |urlencode 過濾 user-controllable ID。
        """
        client, _ = flask_client
        # %27 = ', %22 = ", %3C = <
        r = client.get("/trends?cam_id=%27%22%3Cscript%3E")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # 未編碼的 raw 字元不應出現在 attribute 內
        assert "'\"<script>" not in body, \
            "Critical: ?cam_id= payload 未 URL 編碼 → inline attribute XSS"
        # 編碼後的版本應出現
        assert "data-focus-cam-id=" in body


class TestDashboardTopMissingTrendsLink:
    """Spec G Batch C Task 10：dashboard.html「24h 缺錄最多」表每行加 📈 deep-link。"""

    @staticmethod
    def _seed_top_missing_schema(db_path: str) -> None:
        """建 dashboard 需要的 nvr_servers + cameras + recording_status 表。"""
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recording_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nvr_id INTEGER NOT NULL,
                camera_id TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                completeness REAL NOT NULL,
                missing_seconds REAL NOT NULL DEFAULT 0,
                checked_at TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

    def test_dashboard_top_missing_has_trends_link_per_cam(self, flask_client):
        """dashboard「24h 缺錄最多」表每台 cam 都應有 /trends?cam_id= 連結。"""
        client, db_path = flask_client
        self._seed_top_missing_schema(db_path)
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'missing-cam', 'MissingCam', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.execute(
            "INSERT INTO recording_status "
            "(nvr_id, camera_id, window_start, window_end, completeness, missing_seconds, checked_at) "
            "VALUES (?, 'missing-cam', '2026-08-04T00:00:00Z', '2026-08-05T00:00:00Z', 0.3, 60480.0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()

        r = client.get("/")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # top_missing row 應有 /trends?cam_id=missing-cam&range=24h 連結
        assert re.search(r'href="/trends\?cam_id=missing-cam[^"]*range=24h', body), \
            "top_missing row 應有 /trends?cam_id=...&range=24h deep link"
        # 📈 emoji 應出現
        assert "📈" in body

    def test_dashboard_link_is_xss_safe_for_special_camera_id(self, flask_client):
        """camera_id 含特殊字元 → url_for 自動 URL-encode 防 XSS。"""
        client, db_path = flask_client
        self._seed_top_missing_schema(db_path)
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        # 含 '、"、< 的 camera_id
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, 'XssCam', 0, '2026-08-05T00:00:00Z')",
            (nvr_int, "evil'id\"><script>"),
        )
        conn.execute(
            "INSERT INTO recording_status "
            "(nvr_id, camera_id, window_start, window_end, completeness, missing_seconds, checked_at) "
            "VALUES (?, ?, '2026-08-04T00:00:00Z', '2026-08-05T00:00:00Z', 0.2, 69120.0, '2026-08-05T00:00:00Z')",
            (nvr_int, "evil'id\"><script>"),
        )
        conn.commit()
        conn.close()

        r = client.get("/")
        body = r.data.decode("utf-8")
        # 原始 raw payload 不應出現在 href attribute 內
        assert 'href="/trends?cam_id=evil\'id\"' not in body, \
            "Critical: camera_id 未 URL-encoded 直接拼接 href → XSS"
        # 編碼後的版本應在 href 內
        assert "cam_id=evil" in body, "deep-link 應被 render（即使含特殊字元）"


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
        # Spec G 改用 loop.index0（integer）做 canvas id：跨 NVR 同 device_id 不會撞 id，
        # 且避免 device_id 含特殊字元破壞 HTML id / JS selector。
        # 兩台 cam 各自的 canvas id 應不同：chart-0 vs chart-1
        assert 'id="chart-0"' in body, "CamOnA 應有 chart-0 canvas id"
        assert 'id="chart-1"' in body, "CamOnB 應有 chart-1 canvas id"
        # detail canvas 也用 loop.index0
        assert 'id="chart-detail-0"' in body
        assert 'id="chart-detail-1"' in body
        # 不應再用 device_id 當 canvas id 的一部分
        assert 'id="chart-shared"' not in body, \
            "不應再用 device_id 當 canvas id 的一部分（peer reviewer 修法）"

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


class TestTrendsTemplateLazyDetail:
    """Peer reviewer 2026-08-05 二次報告：detail canvas 0×0 + HTML id 對齊 JS encoder。

    重點修法：
    1. Canvas id 用 loop.index0（純 integer）— 避免特殊字元破壞 HTML id
    2. binLabel 轉台北時區（UTC+8）
    3. Detail canvas lazy init（hidden canvas 預建是 0×0 不可見）
    """

    def _seed_two_cams(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        # 含特殊字元的 device_id 模擬跨 NVR 同 id 衝突場景
        for device_id, name in [
            ("shared/id", "CamShared"),
            ("normal", "CamNormal"),
        ]:
            conn.execute(
                "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
                "VALUES (?, ?, ?, 0, '2026-08-05T00:00:00Z')",
                (nvr_int, device_id, name),
            )
        conn.commit()
        conn.close()

    def test_canvas_id_uses_loop_index_not_raw_cam_id(self, flask_client):
        """Canvas HTML id 應用 loop.index0（integer），不用 raw nvr_id/cam_id。

        修法：避免 HTML id 含特殊字元（/、% 等）破壞 CSS selector / getElementById。
        """
        client, db_path = flask_client
        self._seed_two_cams(db_path)

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 應有 chart-0 / chart-detail-0 / chart-1 / chart-detail-1
        assert 'id="chart-0"' in body
        assert 'id="chart-detail-0"' in body
        assert 'id="chart-1"' in body
        assert 'id="chart-detail-1"' in body
        # 不應有 raw device_id 在 canvas id 內
        assert 'id="chart-shared/id"' not in body, \
            "cam-card canvas id 不應含 raw device_id 特殊字元"
        # data-cam-id 應用 raw form（因為 Jinja urlencode 不編碼 /），
        # JS 端用 CSS.escape 安全處理 selector
        assert 'data-cam-id="shared/id"' in body, \
            "data-cam-id 維持 raw form（urlencode 不編碼 /）+ CSS.escape 安全選擇"
        assert 'data-cam-id="normal"' in body, \
            "data-cam-id 含 normal cam"

    def test_bin_label_uses_taipei_timezone(self, flask_client):
        """binLabel JS 應轉台北 UTC+8（不是 UTC）。

        修法：Spec G 時區慣例（與 fleet、coverage 一致）。
        Python 端不能直接驗 JS 函式，但能驗 JS source 含 Taipei 邏輯：
        - 不應直接用 startUtc.slice(11,16) 純 UTC
        - 應用 Date.parse + UTC+8 offset
        """
        client, _ = flask_client
        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 檢查 JS 含時區轉換邏輯（避免退回 UTC）
        assert "Date.parse" in body, \
            "binLabel 應用 Date.parse 處理 ISO 8601 + UTC offset"
        assert "8 * 3600" in body, \
            "binLabel 應加 8 小時偏移（UTC→台北 UTC+8）"
        # 確認舊的純 UTC 切片邏輯被取代（否則時區不會生效）
        # startUtc.slice 仍可能在別處用，但 binLabel 不應只用 slice
        assert "getUTCMonth" in body or "getUTCDate" in body, \
            "binLabel 應明確從台北時區對應的 Date 物件取 month/date/hour/minute"

    def test_detail_canvas_lazy_init_in_js(self, flask_client):
        """JS 內 detail canvas 應 lazy init（不在 DOMContentLoaded 預建）。

        修法：peer reviewer Critical — hidden canvas 預建會 0×0 不可見。
        Pattern: 在 click handler 內 `if (!canvasDetail.__chart)` 才 new Chart。
        """
        client, db_path = flask_client
        self._seed_two_cams(db_path)
        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # JS 內找 lazy init pattern
        assert "__chart" in body, \
            "JS 端應用 __chart marker 做 lazy init（避免 hidden 0×0）"
        assert "willExpand" in body, \
            "JS 端 toggle 應區分 willExpand（只在 expand 時建 chart）"
        # 不應該在 DOMContentLoaded 內 new Chart for detail canvas（會 0×0）
        # 粗略檢查：DOMContentLoaded 內不該對 chart-detail- 做 populateChart
        dom_content_loaded_section = body.split("DOMContentLoaded")[1].split("});")[0] if "DOMContentLoaded" in body else ""
        # detail canvas 預建 marker 不應出現（canvas.__chart 早就 set 了）
        # 反向檢查：DOMContentLoaded 區段內不該有 chart-detail
        assert "populateChart(makeChart(canvasDetail" not in dom_content_loaded_section, \
            "DOMContentLoaded 不應預建 detail Chart（會 0×0）；應改為 lazy 在 click handler 建"
