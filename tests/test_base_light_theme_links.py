"""驗證 base.html 在 light 模式時為 12 個 theme 各載入對應 light CSS。

補漏：Spec E 完成時發現 light theme link chain 漏 enterprise / glass / gradient /
minimal / cyberpunk / terminal，本測試確保 12 個 theme 都有 light link。
"""
import re
from pathlib import Path


BASE_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"

LIGHT_THEMES = [
    "brutal", "cyberpunk", "earthy", "editorial", "eink", "enterprise",
    "fintech", "glass", "gradient", "minimal", "nordic", "terminal",
]


def test_base_html_loads_all_light_themes():
    """base.html 應為 12 個 theme 各載入對應 light CSS（在 dark *-dark 區塊之外）。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    for theme in LIGHT_THEMES:
        # 期待 'theme == "X"'（只要在 dark 區塊外的 light 區塊）
        pattern = rf"theme\s*==\s*['\"]{re.escape(theme)}['\"]"
        assert re.search(pattern, content), f"base.html 應有 theme == '{theme}' 區塊"
        # 期待 'themes/{theme}.css' light link（注意：要 -dark.css 之外的）
        link_pattern = rf"themes/{re.escape(theme)}\.css"
        matches = re.findall(link_pattern, content)
        # 至少要有一個非 -dark 的 link
        non_dark = [m for m in matches if not m.endswith("-dark.css")]
        assert non_dark, f"base.html 應 link themes/{theme}.css（light 版本）"