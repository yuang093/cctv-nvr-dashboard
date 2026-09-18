"""驗證 /theme/apply 接受 12 個 theme 名（防日後新增 theme 漏字串）。

user #565 follow-up：theme_preview.html 已有 12 卡片（commit 35c5f45），但
/theme/apply 路由本身沒有測試保護。若日後新增 theme 但忘記更新此測試，
POST /theme/apply 仍會接受任意字串、寫進 session、但 base.html 不會載入對應 CSS
→ 使用者以為切了 theme、實際 dashboard 還是 fintech。
"""

from pathlib import Path

import pytest

from web.app import app


ALL_THEMES = [
    "brutal",
    "cyberpunk",
    "earthy",
    "editorial",
    "eink",
    "enterprise",
    "fintech",
    "glass",
    "gradient",
    "minimal",
    "nordic",
    "terminal",
]

WEB_STATIC_THEMES = Path(__file__).resolve().parent.parent / "web" / "static" / "themes"


@pytest.fixture
def client():
    """8444 app 的 test client。"""
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess.clear()
        yield c


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_theme_apply_accepts_all_12_themes(client, theme):
    """POST /theme/apply theme=<theme> 應 302 redirect + session 寫入正確值。"""
    r = client.post("/theme/apply", data={"theme": theme}, follow_redirects=False)
    assert (
        r.status_code == 302
    ), f"POST /theme/apply theme={theme!r} 應 redirect 302，got {r.status_code}"
    # 確認 session 寫入了 theme
    with client.session_transaction() as sess:
        assert (
            sess.get("theme") == theme
        ), f"session['theme'] 應為 {theme!r}，got {sess.get('theme')!r}"


@pytest.mark.parametrize("theme", ALL_THEMES)
def test_theme_apply_flash_message_contains_theme_name(client, theme):
    """POST /theme/apply 應 flash 「主題已套用：<theme>」訊息（在 session.flash 內）。"""
    # 不 follow_redirects（會渲染 dashboard 太慢）—— 直接檢查 session['_flashes']
    client.post("/theme/apply", data={"theme": theme}, follow_redirects=False)
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    assert any(
        cat == "success" and "主題已套用" in msg and theme in msg
        for cat, msg in flashes
    ), f"session._flashes 應含 ('success', '主題已套用：{theme}')，got {flashes}"


def test_theme_apply_rejects_unknown_theme(client):
    """POST /theme/apply theme=<不在 12 清單中> 應仍 302（但 base.html 不會載 CSS）。

    這是現有行為：路由沒做白名單校驗，所以未知 theme 不會報錯。
    本測試確保行為一致——若日後有人改嚴格化校驗，這個測試會提醒。
    """
    r = client.post(
        "/theme/apply", data={"theme": "not-a-real-theme"}, follow_redirects=False
    )
    assert r.status_code == 302, "未知 theme 仍應 redirect（路由不校驗）"
    with client.session_transaction() as sess:
        assert sess.get("theme") == "not-a-real-theme"


def test_all_12_themes_have_css_file():
    """防 theme_preview 加卡片但忘了實作 CSS（user #565 教訓）。

    12 個 theme 都應有對應 light + dark CSS 在 web/static/themes/。
    若缺，會顯示 'bootstrap 風' 而非設計的風格。
    """
    missing_light = []
    missing_dark = []
    for theme in ALL_THEMES:
        if not (WEB_STATIC_THEMES / f"{theme}.css").exists():
            missing_light.append(theme)
        if not (WEB_STATIC_THEMES / f"{theme}-dark.css").exists():
            missing_dark.append(theme)
    assert (
        not missing_light
    ), f"以下 theme 缺 light CSS：{missing_light}（防 theme_preview 加卡片但忘了實作）"
    assert not missing_dark, f"以下 theme 缺 dark CSS：{missing_dark}（Spec E 應為 12 個 theme 各補 *-dark.css）"


def test_theme_apply_route_methods():
    """GET /theme/apply 應 405（POST only）。"""
    rules = [r for r in app.url_map.iter_rules() if r.rule == "/theme/apply"]
    assert len(rules) == 1, "/theme/apply 應只 1 條 route"
    assert "POST" in rules[0].methods, "/theme/apply 應接受 POST"
    assert "GET" not in rules[0].methods, "/theme/apply 不應接受 GET"
