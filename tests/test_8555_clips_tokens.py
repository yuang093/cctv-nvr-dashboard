"""驗證 8555 clips.html 引入 tokens + data-theme + 改用 var()。"""

import re
from pathlib import Path


CLIPS_HTML = (
    Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "clips.html"
)


def test_8555_clips_has_data_theme():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert 'data-theme="{% if dark %}dark{% else %}light{% endif %}"' in content


def test_8555_clips_links_tokens_css():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "tokens.css" in content
    bootstrap_pos = content.find("bootstrap.min.css")
    tokens_pos = content.find("tokens.css")
    assert tokens_pos < bootstrap_pos


def test_8555_clips_dark_block_uses_tokens():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    match = re.search(
        r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL
    )
    assert match, "8555 clips.html 應有 {% if dark %} 區塊"
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert (
        len(hex_matches) == 0
    ), f"8555 clips dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_8555_clips_dark_block_uses_card_token():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "var(--bg-card)" in content
