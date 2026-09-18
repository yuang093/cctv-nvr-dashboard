"""驗證 brutal-dark.css 內容齊全。"""

import re
from pathlib import Path


DARK_CSS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "themes"
    / "brutal-dark.css"
)


def test_dark_file_exists():
    assert DARK_CSS.exists(), f"brutal-dark.css 應存在於 {DARK_CSS}"


def test_dark_has_data_theme_block():
    """必須用 [data-theme="dark"] 觸發。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(
        r'\[data-theme=["\']dark["\']\]\s*\{', content
    ), '應有 [data-theme="dark"] { ... } 區塊'


def test_dark_overrides_accent_tokens():
    """至少覆寫 --primary / --primary-hover / --text-link / --text-link-hover 4 個 accent。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content, f"brutal-dark.css 應覆寫 {token}"


def test_dark_accent_is_brutal_yellow():
    """brutal 品牌色應為亮黃（保留 light theme 特色）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    # 至少 --primary 應為亮黃系
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match, "--primary 應定義"
    color = primary_match.group(1).lower()
    # 接受 #ffff00 / #facc15 等黃系
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    assert r > 200 and g > 180 and b < 100, f"brutal --primary 應為亮黃系，實際 {color}"
