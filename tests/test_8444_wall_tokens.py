"""驗證 8444 wall.html dark CSS 改用 var() 引用 tokens。"""
import re
from pathlib import Path


WALL_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "wall.html"


def test_wall_dark_block_uses_tokens():
    """wall dark CSS 內不應有 hardcode 顏色（除了 rgba 透明色）。"""
    content = WALL_HTML.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match, "wall.html 應有 {% if dark %} 區塊"
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"wall dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_wall_dark_block_uses_bg_primary():
    """wall-card 應用 var(--bg-card)。"""
    content = WALL_HTML.read_text(encoding="utf-8")
    assert "var(--bg-card)" in content, "wall.html 應用 var(--bg-card)"
    assert "var(--border-primary)" in content, "wall.html 應用 var(--border-primary)"


def test_wall_dark_block_preserves_hover_animation():
    """既有 hover 動畫應保留（box-shadow rgba 是透明色例外）。"""
    content = WALL_HTML.read_text(encoding="utf-8")
    assert "box-shadow" in content, "wall hover 動畫應保留"