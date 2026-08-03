"""驗證 8444 fleet.html dark CSS 改用 var() 引用 tokens。"""
import re
from pathlib import Path


FLEET_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "fleet.html"


def test_fleet_dark_block_uses_tokens():
    content = FLEET_HTML.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match, "fleet.html 應有 {% if dark %} 區塊"
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"fleet dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_fleet_dark_block_uses_tokens_card_and_border():
    content = FLEET_HTML.read_text(encoding="utf-8")
    assert "var(--bg-card)" in content
    assert "var(--border-primary)" in content