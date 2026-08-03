"""驗證 8444 web/templates/clips.html dark CSS 改用 var() 引用 tokens。"""
import re
from pathlib import Path


CLIPS_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "clips.html"


def test_8444_clips_dark_block_uses_tokens():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match, "8444 clips.html 應有 {% if dark %} 區塊"
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"8444 clips dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_8444_clips_dark_block_uses_card_and_border_tokens():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "var(--bg-card)" in content
    assert "var(--border-primary)" in content