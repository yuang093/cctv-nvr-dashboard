"""驗證 minimal-dark.css 內容齊全。"""

import re
from pathlib import Path

DARK_CSS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "themes"
    / "minimal-dark.css"
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


def test_dark_accent_is_gray():
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    assert (
        abs(r - g) < 20 and abs(g - b) < 20
    ), f"minimal --primary 應為中性灰，實際 {color}"
