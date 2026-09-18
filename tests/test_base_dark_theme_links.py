"""驗證 base.html 在 dark 模式時為 12 個 theme 各載入對應 *-dark.css。"""

import re
from pathlib import Path


BASE_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"

# 12 個需要 dark CSS 的 theme（fintech-dark 已存在）
DARK_THEMES = [
    "brutal",
    "cyberpunk",
    "earthy",
    "editorial",
    "eink",
    "enterprise",
    "glass",
    "gradient",
    "minimal",
    "nordic",
    "terminal",
    "fintech",
]


def test_base_html_exists():
    assert BASE_HTML.exists()


def test_base_html_loads_all_dark_themes():
    """base.html 應為 12 個 theme 各載入對應 *-dark.css。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    for theme in DARK_THEMES:
        # 期待 'theme == "X"'（順序：dark and theme == X 或 theme == X and dark 皆可）
        pattern = rf"theme\s*==\s*['\"]{re.escape(theme)}['\"]"
        assert re.search(pattern, content), f"base.html 應有 theme == '{theme}' 區塊"
        # 期待 'themes/{theme}-dark.css' link
        link_pattern = rf"themes/{re.escape(theme)}-dark\.css"
        assert re.search(
            link_pattern, content
        ), f"base.html 應 link themes/{theme}-dark.css"


def test_dark_link_loaded_after_fintech_dark():
    """*-dark.css 必須在 fintech-dark.css 之後載入（保證 accent 覆寫最高優先級）。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    fintech_dark_pos = content.find("themes/fintech-dark.css")
    assert fintech_dark_pos > 0, "fintech-dark.css 應存在"
    # 找最晚出現的 *-dark.css link
    last_dark_pos = max(content.find(f"themes/{t}-dark.css") for t in DARK_THEMES)
    assert (
        last_dark_pos > fintech_dark_pos
    ), "所有 *-dark.css 應在 fintech-dark.css 之後"
