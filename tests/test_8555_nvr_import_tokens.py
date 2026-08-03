"""驗證 8555 nvr_import.html 引入 tokens + 改用 var()。"""
import re
from pathlib import Path


NVR_IMPORT = Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "nvr_import.html"


def test_nvr_import_has_data_theme():
    content = NVR_IMPORT.read_text(encoding="utf-8")
    assert 'data-theme="{% if dark %}dark{% else %}light{% endif %}"' in content


def test_nvr_import_links_tokens_css():
    content = NVR_IMPORT.read_text(encoding="utf-8")
    assert "tokens.css" in content


def test_nvr_import_dark_block_uses_tokens():
    content = NVR_IMPORT.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"nvr_import dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"
