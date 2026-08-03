"""驗證 8555 nvr_form.html 引入 tokens + 改用 var()。"""
import re
from pathlib import Path


NVR_FORM = Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "nvr_form.html"


def test_nvr_form_has_data_theme():
    content = NVR_FORM.read_text(encoding="utf-8")
    assert 'data-theme="{% if dark %}dark{% else %}light{% endif %}"' in content


def test_nvr_form_links_tokens_css():
    content = NVR_FORM.read_text(encoding="utf-8")
    assert "tokens.css" in content


def test_nvr_form_dark_block_uses_tokens():
    content = NVR_FORM.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"nvr_form dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_nvr_form_modal_uses_token():
    content = NVR_FORM.read_text(encoding="utf-8")
    assert "var(--bg-modal)" in content, "modal 應用 var(--bg-modal)"
