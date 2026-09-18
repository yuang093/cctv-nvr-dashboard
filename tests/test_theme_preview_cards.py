"""驗證 theme_preview.html 含 12 個 theme 卡片（selectTheme 觸發）。

#565 bug 修法：原本 theme_preview.html 只展示 6 個 theme 卡片（nordic / brutal /
fintech / earthy / editorial / eink），user 沒辦法在 UI 切到 enterprise / glass /
gradient / minimal / cyberpunk / terminal。base.html 已支援 12 個 light/dark
theme CSS，本測試確保 UI 卡片也對齊。
"""

import re
from pathlib import Path


THEME_PREVIEW_HTML = (
    Path(__file__).resolve().parent.parent / "web" / "templates" / "theme_preview.html"
)

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


def test_theme_preview_html_has_all_12_theme_cards():
    """theme_preview.html 應為 12 個 theme 各有一張可點擊卡片。"""
    content = THEME_PREVIEW_HTML.read_text(encoding="utf-8")
    for theme in ALL_THEMES:
        # 期待 selectTheme('<theme>', ...) 出現至少一次
        pattern = rf"selectTheme\(\s*['\"]{re.escape(theme)}['\"]"
        assert re.search(
            pattern, content
        ), f"theme_preview.html 應有 onclick=selectTheme('{theme}', ...) 卡片"


def test_theme_preview_html_cards_have_onclick_handler():
    """每張卡片都應綁 onclick 觸發 selectTheme。"""
    content = THEME_PREVIEW_HTML.read_text(encoding="utf-8")
    cards = re.findall(r'class="theme-card"\s+onclick="([^"]+)"', content)
    assert (
        len(cards) >= 12
    ), f"theme_preview.html 應有 ≥12 張可點擊卡片，目前 {len(cards)} 張"
