"""驗證 8555 nvrs_list.html 引入 tokens + 改用 var()。"""
import re
from pathlib import Path


NVRS_LIST = Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "nvrs_list.html"


def test_nvrs_list_has_data_theme():
    content = NVRS_LIST.read_text(encoding="utf-8")
    assert 'data-theme="{% if dark %}dark{% else %}light{% endif %}"' in content


def test_nvrs_list_links_tokens_css():
    content = NVRS_LIST.read_text(encoding="utf-8")
    assert "tokens.css" in content


def test_nvrs_list_dark_block_uses_tokens():
    content = NVRS_LIST.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"nvrs_list dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_nvrs_list_table_uses_token():
    content = NVRS_LIST.read_text(encoding="utf-8")
    assert "var(--bg-table-striped)" in content, "table 應用 var(--bg-table-striped)"
