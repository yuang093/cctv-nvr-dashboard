"""驗證 eink-dark.css 內容齊全。"""

import re
from pathlib import Path


DARK_CSS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "themes"
    / "eink-dark.css"
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


def test_dark_accent_is_white():
    """eink 品牌色應為純白（e-ink 黑白感）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    assert r > 230 and g > 230 and b > 230, f"eink --primary 應為近白色，實際 {color}"
