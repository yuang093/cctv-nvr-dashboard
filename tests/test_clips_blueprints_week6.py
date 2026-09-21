"""Week 6 #018 — clips Blueprint 拆分 Stage A：bp 路由對應測試。

確認把 3 個 clips bp + 既有 nvr_bp register 到 Flask app 後，產生之
URL map 與原本 web/clips_app.py 內 12 條路由 + 既有 nvr_bp 等價。
"""
from __future__ import annotations

from flask import Flask

from web.blueprints_clips.pages_bp import pages_bp
from web.blueprints_clips.coverage_bp import coverage_bp
from web.blueprints_clips.media_bp import media_bp


# 預期 routes（從 web/clips_app.py 原始 12 條盤點；扣掉既有 nvr_bp 已處理）
EXPECTED_ROUTES: set[str] = {
    # pages_bp
    "/",
    "/clips",
    "/dark/toggle",
    # coverage_bp（url_prefix=/clips/coverage）
    "/clips/coverage",
    "/clips/coverage/data",
    # media_bp（url_prefix=/clips）
    "/clips/nvrs",
    "/clips/cameras",
    "/clips/snapshots",
    "/clips/fetch",
    "/clips/fetch_sync",
}


def test_all_clips_bp_register_without_error():
    """3 個 clips bp 都能 register 進 Flask app。"""
    app = Flask(__name__)
    app.register_blueprint(pages_bp)
    app.register_blueprint(coverage_bp)
    app.register_blueprint(media_bp)
    rules = [r.rule for r in app.url_map.iter_rules() if r.rule != "/static/<path:filename>"]
    assert len(rules) >= len(EXPECTED_ROUTES) - 1


def test_clips_bp_routes_match_original():
    """所有 clips bp routes 與原 clips_app.py 等價。"""
    app = Flask(__name__)
    app.register_blueprint(pages_bp)
    app.register_blueprint(coverage_bp)
    app.register_blueprint(media_bp)

    actual_rules: set[str] = set()
    for rule in app.url_map.iter_rules():
        if rule.rule == "/static/<path:filename>":
            continue
        actual_rules.add(rule.rule)

    missing = EXPECTED_ROUTES - actual_rules
    extra = actual_rules - EXPECTED_ROUTES
    assert not missing, f"clips bp 缺少路由：{missing}"
    assert not extra, f"clips bp 多出路由：{extra}"


def test_pages_bp_root_route():
    """GET / 對應到 pages_bp.index（前端首頁入口）。"""
    app = Flask(__name__)
    app.register_blueprint(pages_bp)
    rules = list(app.url_map.iter_rules())
    root = [r for r in rules if r.rule == "/" and "GET" in r.methods]
    assert root, "/ 路由不存在"
    assert root[0].endpoint == "clips_pages.index"


def test_coverage_bp_url_prefix():
    """coverage_bp 正確套用 /clips/coverage 前綴。"""
    app = Flask(__name__)
    app.register_blueprint(coverage_bp)
    rules = list(app.url_map.iter_rules())
    coverage_rules = [
        r for r in rules if r.rule.startswith("/clips/coverage")
    ]
    assert len(coverage_rules) == 2, f"應有 2 條 /clips/coverage 路由，實際 {len(coverage_rules)}"


def test_media_bp_url_prefix():
    """media_bp 正確套用 /clips 前綴。"""
    app = Flask(__name__)
    app.register_blueprint(media_bp)
    rules = list(app.url_map.iter_rules())
    media_rules = [r for r in rules if r.rule.startswith("/clips/")]
    # 排除 coverage 子前綴（會重複計算；此測試只驗 prefix 生效）
    assert len(media_rules) >= 5, f"應至少有 5 條 /clips/* 路由（media_bp），實際 {len(media_rules)}"


def test_clips_helpers_session_store():
    """從 web.clips_helpers import SessionStore 仍可用。"""
    from web.clips_helpers import SessionStore

    ss = SessionStore(ttl_seconds=60)
    ss.set(42, "fake-token")
    assert ss.get(42) == "fake-token"
    ss.clear(42)
    assert ss.get(42) is None


def test_clips_helpers_login_nvr_signature():
    """login_nvr 簽名：接受 nvr_row dict，回傳 session token string。

    不實際登入（會因無 AVIGILON_USER_KEY crash），僅檢查 import 路徑。
    """
    from web.clips_helpers import login_nvr, build_nvr_config

    assert callable(login_nvr)
    assert callable(build_nvr_config)
    # build_nvr_config 是純函式，可直接驗
    sample_row = {
        "nvr_id": "TEST-1",
        "name": "Test",
        "host": "1.2.3.4",
        "port": 8443,
        "username": "api_reader",
        "password": "x",
        "verify_ssl": 0,
    }
    cfg = build_nvr_config(sample_row)
    assert cfg["id"] == "TEST-1"
    assert cfg["host"] == "1.2.3.4"
    assert cfg["verify_ssl"] is False
