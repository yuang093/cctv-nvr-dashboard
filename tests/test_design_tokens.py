"""驗證 web/static/css/tokens.css 內容齊全。"""
import os
import re
from pathlib import Path

import pytest

TOKENS_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "css" / "tokens.css"


@pytest.fixture(scope="module")
def tokens_content():
    """讀取 tokens.css 完整內容。"""
    assert TOKENS_CSS.exists(), f"tokens.css 應存在於 {TOKENS_CSS}"
    return TOKENS_CSS.read_text(encoding="utf-8")


# === 檔案存在 + 結構 ===

def test_tokens_file_exists():
    assert TOKENS_CSS.exists()


def test_tokens_has_root_block(tokens_content):
    """淺色 token 應在 :root 內。"""
    assert ":root" in tokens_content
    assert re.search(r":root\s*\{", tokens_content), "應有 :root { ... } 區塊"


def test_tokens_has_dark_block(tokens_content):
    """深色 token 應在 [data-theme="dark"] 內。"""
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', tokens_content), \
        '應有 [data-theme="dark"] { ... } 區塊'


# === 必填色票（淺 + 深）===

REQUIRED_COLOR_TOKENS = [
    # 背景
    "--bg-primary", "--bg-card", "--bg-input", "--bg-table-striped",
    "--bg-modal", "--bg-secondary",
    # 文字
    "--text-primary", "--text-muted", "--text-link", "--text-link-hover", "--text-code",
    # 邊框
    "--border-primary", "--border-secondary",
    # 狀態
    "--primary", "--primary-hover",
    "--danger-bg", "--danger-text", "--danger-border",
    "--warning-bg", "--warning-text", "--warning-border",
    "--success-bg", "--success-text", "--success-border",
]


@pytest.mark.parametrize("token", REQUIRED_COLOR_TOKENS)
def test_color_token_defined_in_both_themes(tokens_content, token):
    """每個色票 token 應在淺色跟深色都定義。"""
    light_match = re.search(r":root\s*\{([^}]*)\}", tokens_content, re.DOTALL)
    dark_match = re.search(r'\[data-theme=["\']dark["\']\]\s*\{([^}]*)\}', tokens_content, re.DOTALL)
    assert light_match, ":root 區塊缺失"
    assert dark_match, "[data-theme=dark] 區塊缺失"
    assert token in light_match.group(1), f"{token} 應在 :root 內"
    assert token in dark_match.group(1), f"{token} 應在 [data-theme=dark] 內"


# === 字體 ===

def test_font_family_base_defined(tokens_content):
    assert "--font-family-base" in tokens_content


def test_font_family_mono_defined(tokens_content):
    assert "--font-family-mono" in tokens_content


def test_font_size_scale_defined(tokens_content):
    """字體階梯至少 6 級（xs/sm/base/lg/xl/2xl）。"""
    for size in ["xs", "sm", "base", "lg", "xl", "2xl"]:
        assert f"--font-size-{size}" in tokens_content, f"--font-size-{size} 缺失"


# === 間距 ===

def test_spacing_scale_defined(tokens_content):
    """間距階梯 1-6 (4px 進位)。"""
    for n in range(1, 7):
        assert f"--spacing-{n}" in tokens_content, f"--spacing-{n} 缺失"


# === 圓角 ===

def test_radius_scale_defined(tokens_content):
    """圓角階梯。"""
    for r in ["sm", "md", "lg"]:
        assert f"--radius-{r}" in tokens_content, f"--radius-{r} 缺失"


# === 值合理性（淺色預設 + 深色覆寫）===

def test_light_bg_primary_is_light(tokens_content):
    """淺色背景應為近白色。"""
    light_block = re.search(r":root\s*\{([^}]*)\}", tokens_content, re.DOTALL).group(1)
    match = re.search(r"--bg-primary:\s*(#\w+|rgb\([^)]+\)|rgba\([^)]+\))", light_block)
    assert match, "--bg-primary 應在 :root 內"
    value = match.group(1).lower()
    if value.startswith("#"):
        hex_val = value.lstrip("#")
        r = int(hex_val[0:2], 16)
        assert r >= 200, f"淺色 --bg-primary 應為近白色，實際 {value}"


def test_dark_bg_primary_is_dark(tokens_content):
    """深色背景應為近黑色。"""
    dark_block = re.search(r'\[data-theme=["\']dark["\']\]\s*\{([^}]*)\}', tokens_content, re.DOTALL).group(1)
    match = re.search(r"--bg-primary:\s*(#\w+|rgb\([^)]+\)|rgba\([^)]+\))", dark_block)
    assert match, "--bg-primary 應在 [data-theme=dark] 內"
    value = match.group(1).lower()
    if value.startswith("#"):
        hex_val = value.lstrip("#")
        r = int(hex_val[0:2], 16)
        assert r <= 50, f"深色 --bg-primary 應為近黑色，實際 {value}"
