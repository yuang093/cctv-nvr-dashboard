"""Week 6 #017 — Blueprint 拆分 Stage A：bp 路由對應測試。

確認把 5 個 bp register 到 Flask app 後，產生之 URL map 與
原本 web/app.py 內 `_register_routes()` 內 35 條路由等價。

Stage B 後此測試仍保留 — 已成為藍圖結構的迴歸保護。
"""
from __future__ import annotations

import pytest
from flask import Flask

from web.blueprints.dashboard_bp import dashboard_bp
from web.blueprints.nvrs_bp import nvrs_bp
from web.blueprints.runs_bp import runs_bp
from web.blueprints.scan_bp import scan_bp
from web.blueprints.devices_bp import devices_bp


# 預期所有 routes（從 web/app.py _register_routes 原始盤點）
EXPECTED_ROUTES: set[str] = {
    # dashboard_bp
    "/",
    "/theme",
    "/theme/apply",
    "/fleet",
    "/dark/toggle",
    # runs_bp
    "/runs",
    "/reports",
    "/query",
    # scan_bp
    "/scan",
    "/scan/status",
    "/dashboard/refresh-completeness",
    "/dashboard/refresh-completeness/status",
    # devices_bp
    "/wall",
    "/devices",
    "/devices/discover",
    "/trends",
    "/events",
    "/abnormal",
    "/abnormal/export.pdf",
    # 動態部分
    "/runs/<int:run_id>",
    "/reports/download/<int:run_id>",
    "/devices/discover/<int:session_id>",
    "/devices/<device_id>",
    "/health/cameras/<device_id>",
    # nvrs_bp（url_prefix=/nvrs，所有 path 加 prefix）
    "/nvrs",
    "/nvrs/<int:internal_id>/toggle",
    "/nvrs/new",
    "/nvrs/<int:nvr_id>/edit",
    "/nvrs/<int:nvr_id>/delete",
    "/nvrs/test-connection",
    "/nvrs/import",
    "/nvrs/import/template.csv",
    "/nvrs/import/template.json",
    "/nvrs/export.csv",
    "/nvrs/export.json",
}


@pytest.fixture
def bp_app() -> Flask:
    """建 Flask app 並 register 全部 5 bp，回傳 app 與 url_map。"""
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(nvrs_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(devices_bp)
    return app


def test_all_bp_register_without_error():
    """5 個 bp 都能 register 進 Flask app，無 endpoint 重複。"""
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(nvrs_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(devices_bp)
    # 若 endpoint 重複會 raise ValueError
    rules = [r.rule for r in app.url_map.iter_rules() if r.rule != "/static/<path:filename>"]
    assert len(rules) >= len(EXPECTED_ROUTES) - 1  # 容忍一個動態差異


def test_bp_routes_match_original():
    """所有 bp routes 與原 _register_routes 35 條 URL 完全相符。"""
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(nvrs_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(devices_bp)

    actual_rules: set[str] = set()
    for rule in app.url_map.iter_rules():
        if rule.rule == "/static/<path:filename>":
            continue
        actual_rules.add(rule.rule)

    missing = EXPECTED_ROUTES - actual_rules
    extra = actual_rules - EXPECTED_ROUTES
    assert not missing, f"bp 缺少路由：{missing}"
    assert not extra, f"bp 多出路由：{extra}"


def test_dashboard_bp_root_route():
    """GET / 對應到 dashboard_bp.dashboard。"""
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    rules = list(app.url_map.iter_rules())
    dashboard_root = [r for r in rules if r.rule == "/" and "GET" in r.methods]
    assert dashboard_root, "/ 路由不存在"
    assert dashboard_root[0].endpoint == "dashboard.dashboard"


def test_nvrs_bp_url_prefix():
    """nvrs_bp 正確套用 /nvrs 前綴。"""
    app = Flask(__name__)
    app.register_blueprint(nvrs_bp)
    rules = list(app.url_map.iter_rules())
    nvrs_rules = [r for r in rules if r.rule.startswith("/nvrs")]
    assert len(nvrs_rules) == 11, f"應有 11 條 /nvrs 路由，實際 {len(nvrs_rules)}"
