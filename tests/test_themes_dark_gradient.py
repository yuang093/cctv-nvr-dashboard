"""驗證 gradient-dark.css 內容齊全。"""

import re
from pathlib import Path


DARK_CSS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "themes"
    / "gradient-dark.css"
)


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_purple_or_blue():
    """gradient 品牌色：紫→藍漸層。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # 紫（#8b5cf6）或 藍（#3b82f6）皆可
    is_purple = r > g and b > g
    is_blue = b > 200 and b > r and b > g
    assert is_purple or is_blue, f"gradient --primary 應為紫或藍系，實際 {color}"
