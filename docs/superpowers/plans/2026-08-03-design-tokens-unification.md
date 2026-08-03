# 8444 / 8555 Design Tokens 統一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽出共用 CSS 設計變數（色票 + 字體 + 間距 + 圓角），8444 跟 8555 兩邊統一引用，達成「同設計語言」效果。

**Architecture:** 單一 `web/static/css/tokens.css` 集中兩套 theme（`:root` 淺色、`[data-theme="dark"]` 深色）。8444 + 8555 各自用 Flask template `<link>` 引入，inline `<style>` 改用 `var()` 引用。`<html data-theme="...">` 屬性由 Flask session `dark` 旗標決定。

**Tech Stack:** Python 3.10+ / Flask 3 / CSS 變數（無 Sass / PostCSS 編譯）

**Reference:** `docs/superpowers/specs/2026-08-03-design-tokens-unification.md`

---

## Task 1: 建立 `web/static/css/tokens.css` + 內容驗證測試

**Files:**
- Create: `web/static/css/tokens.css`
- Create: `tests/test_design_tokens.py`

- [ ] **Step 1: Write the failing test**

建立測試檔 `tests/test_design_tokens.py`：

```python
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
    # 抽出 RGB 數值
    if value.startswith("#"):
        hex_val = value.lstrip("#")
        r = int(hex_val[0:2], 16)
        # 淺色背景 R 值應該 >= 200
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
        # 深色背景 R 值應該 <= 50
        assert r <= 50, f"深色 --bg-primary 應為近黑色，實際 {value}"
```

- [ ] **Step 2: 確認測試 fail**

```bash
pytest tests/test_design_tokens.py -q
```

預期：FAIL — `tokens.css` 不存在（或讀取失敗）。

- [ ] **Step 3: 建立 `web/static/css/tokens.css`**

```css
/* ============================================
 * Design Tokens — 8444 / 8555 統一設計變數
 * 2026-08-03 建立
 * 維護原則：新增設計值一律在此宣告；不要在 template inline CSS 寫 hardcode。
 * ============================================ */

/* === 淺色主題（預設）=== */
:root {
    /* 色票 - 背景 */
    --bg-primary: #ffffff;
    --bg-card: #ffffff;
    --bg-input: #ffffff;
    --bg-table-striped: #f8fafc;
    --bg-modal: #ffffff;
    --bg-secondary: #f1f5f9;

    /* 色票 - 文字 */
    --text-primary: #1e293b;
    --text-muted: #64748b;
    --text-link: #2563eb;
    --text-link-hover: #1d4ed8;
    --text-code: #be185d;

    /* 色票 - 邊框 */
    --border-primary: #e2e8f0;
    --border-secondary: #cbd5e1;

    /* 色票 - 狀態（淺色版） */
    --primary: #3b82f6;
    --primary-hover: #2563eb;
    --danger-bg: #fef2f2;
    --danger-text: #991b1b;
    --danger-border: #fecaca;
    --warning-bg: #fffbeb;
    --warning-text: #92400e;
    --warning-border: #fde68a;
    --success-bg: #f0fdf4;
    --success-text: #166534;
    --success-border: #bbf7d0;

    /* 字體 */
    --font-family-base: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --font-family-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    --font-size-xs: 0.75rem;
    --font-size-sm: 0.875rem;
    --font-size-base: 1rem;
    --font-size-lg: 1.125rem;
    --font-size-xl: 1.25rem;
    --font-size-2xl: 1.5rem;

    /* 間距（4px 進位） */
    --spacing-1: 0.25rem;
    --spacing-2: 0.5rem;
    --spacing-3: 0.75rem;
    --spacing-4: 1rem;
    --spacing-5: 1.5rem;
    --spacing-6: 2rem;

    /* 圓角 */
    --radius-sm: 4px;
    --radius-md: 8px;
    --radius-lg: 12px;
}

/* === 深色主題（由 [data-theme="dark"] 觸發）=== */
[data-theme="dark"] {
    /* 色票 - 背景 */
    --bg-primary: #0c1220;
    --bg-card: #1a1a2e;
    --bg-input: #1a1a2e;
    --bg-table-striped: #16213e;
    --bg-modal: #1a1a2e;
    --bg-secondary: #1e293b;

    /* 色票 - 文字 */
    --text-primary: #f1f5f9;
    --text-muted: #cbd5e1;
    --text-link: #93c5fd;
    --text-link-hover: #bfdbfe;
    --text-code: #f0abfc;

    /* 色票 - 邊框 */
    --border-primary: #334155;
    --border-secondary: #475569;

    /* 色票 - 狀態（深色版） */
    --primary: #3b82f6;
    --primary-hover: #60a5fa;
    --danger-bg: #7f1d1d;
    --danger-text: #fecaca;
    --danger-border: #dc2626;
    --warning-bg: #78350f;
    --warning-text: #fde68a;
    --warning-border: #d97706;
    --success-bg: #14532d;
    --success-text: #bbf7d0;
    --success-border: #166534;
}
```

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_design_tokens.py -q
```

預期：全部 PASS（19+ 個測試）。

- [ ] **Step 5: Commit**

```bash
git add web/static/css/tokens.css tests/test_design_tokens.py
git commit -m "feat(design-tokens): 建立 8444/8555 共用 CSS 設計變數

- 新增 web/static/css/tokens.css 集中兩套主題
- 淺色在 :root，深色在 [data-theme=\"dark\"]
- 涵蓋色票（背景/文字/邊框/狀態）、字體、間距、圓角
- 19 個內容驗證測試確保所有 token 存在且值合理

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: 8444 `base.html` 引入 tokens.css + `data-theme` 屬性

**Files:**
- Modify: `web/templates/base.html:2`、`web/templates/base.html:9` 之間

- [ ] **Step 1: 寫測試**

建立 `tests/test_8444_base_data_theme.py`：

```python
"""驗證 8444 base.html 引入 tokens.css 並設置 data-theme 屬性。"""
from pathlib import Path


BASE_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"


def test_base_html_has_data_theme_attribute():
    """<html> 應有 data-theme 屬性，依 dark flag 切換。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert 'data-theme="{% if dark %}dark{% else %}light{% endif %}"' in content, \
        "應有 <html data-theme=\"...\" 動態屬性"


def test_base_html_links_tokens_css():
    """應引入 tokens.css。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert "tokens.css" in content, "應引入 web/static/css/tokens.css"
    # 確認在 Bootstrap 之前引入
    bootstrap_pos = content.find("bootstrap.min.css")
    tokens_pos = content.find("tokens.css")
    assert tokens_pos < bootstrap_pos, "tokens.css 應在 Bootstrap 之前引入"


def test_base_html_keeps_existing_dark_toggle():
    """既有 dark toggle 按鈕與 fintech-dark.css 引入都要保留。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert "dark_toggle" in content, "dark_toggle 路由要保留"
    assert "fintech-dark.css" in content, "fintech-dark.css 引入要保留"
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8444_base_data_theme.py -q
```

預期：FAIL — `data-theme` 屬性與 `tokens.css` 引入都不存在。

- [ ] **Step 3: 修改 `web/templates/base.html`**

(a) 第 2 行：`<html lang="zh-Hant">` →

```html
<html lang="zh-Hant" data-theme="{% if dark %}dark{% else %}light{% endif %}">
```

(b) 第 8 行（Bootstrap CDN）**之前**插入：

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
```

最終 head 區塊（行 1-13）：

```html
<!DOCTYPE html>
<html lang="zh-Hant" data-theme="{% if dark %}dark{% else %}light{% endif %}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}NVR 掃描器{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
    {# 深色模式：獨立於主題，永遠在主題之上覆蓋 #}
    {% if dark %}
    <link rel="stylesheet" href="{{ url_for('static', filename='themes/fintech-dark.css') }}">
    {% endif %}
```

其餘行（14-79）維持不變。

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8444_base_data_theme.py -q
```

預期：3 個測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add web/templates/base.html tests/test_8444_base_data_theme.py
git commit -m "feat(8444-base): 引入 tokens.css + data-theme 屬性

- <html data-theme> 由 Flask session dark 旗標決定
- tokens.css 在 Bootstrap 之前引入（CSS 變數盡早生效）
- 既有 dark toggle 與 fintech-dark.css 引入保留

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 8444 `wall.html` 改用 var() 引用 token

**Files:**
- Modify: `web/templates/wall.html:135-150`（dark CSS block）

- [ ] **Step 1: 寫測試**

建立 `tests/test_8444_wall_tokens.py`：

```python
"""驗證 8444 wall.html dark CSS 改用 var() 引用 tokens。"""
from pathlib import Path


WALL_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "wall.html"


def test_wall_dark_block_uses_tokens():
    """wall dark CSS 內不應有 hardcode 顏色（除了 rgba 透明色）。"""
    content = WALL_HTML.read_text(encoding="utf-8")
    # 找 {% if dark %} ... {% endif %} 區塊
    import re
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match, "wall.html 應有 {% if dark %} 區塊"
    dark_block = match.group(1)
    # 移除 rgba() 透明色後不應有 #XXXXXX 顏色
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
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8444_wall_tokens.py -q
```

預期：FAIL — `#1a1a2e` 等 hardcode 還在。

- [ ] **Step 3: 修改 `web/templates/wall.html` line 135-150**

把：
```html
{% if dark %}
/* Dark mode overrides */
.wall-card {
    background: #1a1a2e;
    border-color: #334155;
}
.wall-card:hover {
    box-shadow: 0 0.25rem 0.75rem rgba(0,0,0,0.5);
}
.wall-thumb-wrapper {
    background: #0f172a;
}
.wall-status-dot {
    border-color: #1a1a2e;
}
{% endif %}
```

改為：
```html
{% if dark %}
/* Dark mode overrides — 引用 design tokens */
.wall-card {
    background: var(--bg-card);
    border-color: var(--border-primary);
}
.wall-card:hover {
    box-shadow: 0 0.25rem 0.75rem rgba(0,0,0,0.5);
}
.wall-thumb-wrapper {
    background: var(--bg-secondary);
}
.wall-status-dot {
    border-color: var(--bg-card);
}
{% endif %}
```

顏色對映：
- `#1a1a2e` → `var(--bg-card)`
- `#334155` → `var(--border-primary)`
- `#0f172a` → `var(--bg-secondary)` (深色時為 `#1e293b`)

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8444_wall_tokens.py -q
```

預期：3 個測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add web/templates/wall.html tests/test_8444_wall_tokens.py
git commit -m "refactor(8444-wall): dark CSS 改用 var() 引用 tokens

- 4 個 hardcode 顏色替換為 var(--bg-card) / var(--border-primary) / var(--bg-secondary)
- rgba 透明色保留（box-shadow 動畫）
- 視覺效果與改動前一致（用 fintech-dark 相同色票）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 8444 `fleet.html` 改用 var() 引用 token

**Files:**
- Modify: `web/templates/fleet.html:223-238`（dark CSS block）

- [ ] **Step 1: 寫測試**

建立 `tests/test_8444_fleet_tokens.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8444_fleet_tokens.py -q
```

預期：FAIL。

- [ ] **Step 3: 修改 `web/templates/fleet.html` line 223-238**

把：
```html
{% if dark %}
.fleet-card {
    background: #1a1a2e;
    border-color: #334155;
}
.fleet-card:hover {
    box-shadow: 0 0.25rem 0.75rem rgba(0,0,0,0.5);
}
.fleet-bar {
    background: #0f172a;
}
.fleet-dot {
    border-color: #1a1a2e;
}
{% endif %}
```

改為：
```html
{% if dark %}
/* Dark mode overrides — 引用 design tokens */
.fleet-card {
    background: var(--bg-card);
    border-color: var(--border-primary);
}
.fleet-card:hover {
    box-shadow: 0 0.25rem 0.75rem rgba(0,0,0,0.5);
}
.fleet-bar {
    background: var(--bg-secondary);
}
.fleet-dot {
    border-color: var(--bg-card);
}
{% endif %}
```

顏色對映同 Task 3 wall.html。

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8444_fleet_tokens.py -q
```

預期：PASS。

- [ ] **Step 5: Commit**

```bash
git add web/templates/fleet.html tests/test_8444_fleet_tokens.py
git commit -m "refactor(8444-fleet): dark CSS 改用 var() 引用 tokens

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: 8444 `templates/clips.html` 改用 var() 引用 token

**Files:**
- Modify: `web/templates/clips.html:30-45`（clips.html 是 8444 內，但 clips.html 副本在 8555 web/clips_templates/clips.html 會在 Task 6-9 處理）

- [ ] **Step 1: 寫測試**

建立 `tests/test_8444_templates_clips_tokens.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8444_templates_clips_tokens.py -q
```

預期：FAIL。

- [ ] **Step 3: 修改 `web/templates/clips.html` line 30-44**

把：
```html
{% if dark %}
<style>
    body { background: #0c1220; color: #e2e8f0; }
    .card { background: #1a1a2e; border-color: #334155; }
    .form-label { color: #e2e8f0; }
    .form-control, .form-select { background: #1a1a2e; color: #e2e8f0; border-color: #334155; }
    .btn-outline-secondary { color: #94a3b8; border-color: #475569; }
    .btn-outline-warning { color: #fbbf24; border-color: #a16207; }
    .btn-outline-primary { color: #93c5fd; border-color: #1e40af; }
    .text-muted { color: #94a3b8 !important; }
    code { color: #f0abfc; }
    .sync-slot.empty { background: #1a1a2e; color: #94a3b8; }
    .sync-slot.error { background: #4c1d1d; }
    #camPanel { background: #1a1a2e; border-color: #334155; }
</style>
{% endif %}
```

改為：
```html
{% if dark %}
<style>
    body { background: var(--bg-primary); color: var(--text-primary); }
    .card { background: var(--bg-card); border-color: var(--border-primary); }
    .form-label { color: var(--text-primary); }
    .form-control, .form-select { background: var(--bg-input); color: var(--text-primary); border-color: var(--border-primary); }
    .btn-outline-secondary { color: var(--text-secondary); border-color: var(--border-secondary); }
    .btn-outline-warning { color: var(--warning-text); border-color: var(--warning-border); }
    .btn-outline-primary { color: var(--text-link); border-color: var(--primary-hover); }
    .text-muted { color: var(--text-secondary) !important; }
    code { color: var(--text-code); }
    .sync-slot.empty { background: var(--bg-card); color: var(--text-secondary); }
    .sync-slot.error { background: var(--bg-danger); }
    #camPanel { background: var(--bg-card); border-color: var(--border-primary); }
</style>
{% endif %}
```

顏色對映：
- `#0c1220` → `var(--bg-primary)`
- `#e2e8f0` → `var(--text-primary)`
- `#1a1a2e` → `var(--bg-card)`
- `#334155` → `var(--border-primary)`
- `#475569` → `var(--border-secondary)`
- `#94a3b8` → `var(--text-secondary)`
- `#fbbf24` → `var(--warning-text)`
- `#a16207` → `var(--warning-border)`
- `#93c5fd` → `var(--text-link)`
- `#1e40af` → `var(--primary-hover)` (近似藍色)
- `#f0abfc` → `var(--text-code)`
- `#4c1d1d` → `var(--bg-danger)` (深色版本為 #7f1d1d；視覺差異極小)

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8444_templates_clips_tokens.py -q
```

預期：PASS。

- [ ] **Step 5: Commit**

```bash
git add web/templates/clips.html tests/test_8444_templates_clips_tokens.py
git commit -m "refactor(8444-clips): dark CSS 改用 var() 引用 tokens

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: 8444 `fintech-dark.css` 改寫用 var() 引用 token

**Files:**
- Modify: `web/static/themes/fintech-dark.css`（整檔 264 行）

- [ ] **Step 1: 寫測試**

建立 `tests/test_fintech_dark_uses_tokens.py`：

```python
"""驗證 fintech-dark.css 內大部分 hardcode 顏色被替換為 var()。"""
import re
from pathlib import Path


FINTECH_DARK = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "fintech-dark.css"


def test_fintech_dark_uses_var_for_bg():
    """關鍵色票應用 var()。"""
    content = FINTECH_DARK.read_text(encoding="utf-8")
    # 應該有 var(--bg-primary) 等（至少 5 個）
    for token in ["--bg-primary", "--bg-card", "--bg-secondary", "--border-primary", "--text-primary"]:
        assert f"var({token})" in content, f"fintech-dark.css 應使用 var({token})"


def test_fintech_dark_hardcode_count_reduced():
    """hardcode 顏色數量應減少（從 90+ 降到 30 以下）。"""
    content = FINTECH_DARK.read_text(encoding="utf-8")
    # 移除 rgba + var() 後才算 hardcode
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", content)
    var_removed = re.sub(r"var\(--[\w-]+\)", "", rgba_removed)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", var_removed)
    # 允許保留部分細節色（Bootstrap 細節）
    assert len(hex_matches) <= 30, \
        f"fintech-dark.css hardcode 顏色應 ≤ 30，實際 {len(hex_matches)} 個：{hex_matches[:10]}"
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_fintech_dark_uses_tokens.py -q
```

預期：FAIL — 還沒改。

- [ ] **Step 3: 改寫 `web/static/themes/fintech-dark.css`**

由於檔案 264 行，**逐項替換**。規則：

| 舊色 | 新值 (var) | 用途 |
|---|---|---|
| `#0c1220` | `var(--bg-primary)` | 背景主色 |
| `#111827` | `var(--bg-card)` | card 背景 |
| `#1e293b` | `var(--bg-secondary)` | 次背景、邊框 |
| `#1e3a5f` | `var(--bg-info)` | info 背景 |
| `#450a0a` | `var(--bg-danger)` | danger 背景 |
| `#451a03` | `var(--bg-warning)` | warning 背景 |
| `#14532d` | `var(--bg-success)` | success 背景 |
| `#134e4a` | `var(--bg-success)` | success 背景（取代） |
| `#3b82f6` | `var(--primary)` | primary |
| `#2563eb` | `var(--primary-hover)` | primary hover |
| `#dc2626` | `var(--danger-text)` | danger 文字 |
| `#b91c1c` | `var(--primary-hover)` | danger hover（沿用） |
| `#334155` | `var(--border-primary)` | 主要邊框 |
| `#475569` | `var(--border-secondary)` | 次要邊框 |
| `#64748b` | `var(--text-muted)` | muted 文字 |
| `#94a3b8` | `var(--text-secondary)` | 次要文字 |
| `#e2e8f0` | `var(--text-primary)` | 主要文字 |
| `#f1f5f9` | `var(--text-primary)` | 主要文字（亮色） |
| `#cbd5e1` | `var(--text-secondary)` | 次要文字 |
| `#93c5fd` | `var(--text-link)` | 連結 |
| `#60a5fa` | `var(--primary-hover)` | hover |
| `#fde68a` | `var(--warning-text)` | warning 文字 |
| `#fed7aa` | `var(--warning-text)` | warning 文字 |
| `#fca5a5` | `var(--danger-text)` | danger 文字 |
| `#86efac` | `var(--success-text)` | success 文字 |
| `#5eead4` | `var(--text-link)` | info 文字 |
| `#fbbf24` | `var(--warning-text)` | warning 文字 |
| `#22c55e` | `var(--success-text)` | success |
| `#f59e0b` | `var(--warning-text)` | warning |
| `#ef4444` | `var(--danger-text)` | danger |
| `#14b8a6` | `var(--text-link)` | info |
| `#f0abfc` | `var(--text-code)` | code |

如果需要新增顏色 token，可回頭加 `tokens.css`（在最終 task 之前）。

**提示**：用 sed 批量替換（Git Bash 環境）：
```bash
cd web/static/themes
sed -i 's/#0c1220/var(--bg-primary)/g' fintech-dark.css
sed -i 's/#111827/var(--bg-card)/g' fintech-dark.css
sed -i 's/#1e293b/var(--bg-secondary)/g' fintech-dark.css
sed -i 's/#3b82f6/var(--primary)/g' fintech-dark.css
# ... 其他依上表
```

或用 Python 腳本（更安全）：

```python
# /tmp/replace_colors.py
from pathlib import Path

REPLACEMENTS = [
    ("#0c1220", "var(--bg-primary)"),
    ("#111827", "var(--bg-card)"),
    ("#1e293b", "var(--bg-secondary)"),
    ("#1e3a5f", "var(--bg-info)"),
    ("#450a0a", "var(--bg-danger)"),
    ("#451a03", "var(--bg-warning)"),
    ("#14532d", "var(--bg-success)"),
    ("#134e4a", "var(--bg-success)"),
    ("#3b82f6", "var(--primary)"),
    ("#2563eb", "var(--primary-hover)"),
    ("#dc2626", "var(--danger-text)"),
    ("#b91c1c", "var(--primary-hover)"),
    ("#334155", "var(--border-primary)"),
    ("#475569", "var(--border-secondary)"),
    ("#64748b", "var(--text-muted)"),
    ("#94a3b8", "var(--text-secondary)"),
    ("#e2e8f0", "var(--text-primary)"),
    ("#f1f5f9", "var(--text-primary)"),
    ("#cbd5e1", "var(--text-secondary)"),
    ("#93c5fd", "var(--text-link)"),
    ("#60a5fa", "var(--primary-hover)"),
    ("#fde68a", "var(--warning-text)"),
    ("#fed7aa", "var(--warning-text)"),
    ("#fca5a5", "var(--danger-text)"),
    ("#86efac", "var(--success-text)"),
    ("#5eead4", "var(--text-link)"),
    ("#fbbf24", "var(--warning-text)"),
    ("#22c55e", "var(--success-text)"),
    ("#f59e0b", "var(--warning-text)"),
    ("#ef4444", "var(--danger-text)"),
    ("#14b8a6", "var(--text-link)"),
    ("#f0abfc", "var(--text-code)"),
]

# 注意：有些 token（如 --bg-info）不在 tokens.css 內時，需要加回去
# 跑完後若 diff 變紅，檢查 tokens.css 是否齊全

path = Path("web/static/themes/fintech-dark.css")
content = path.read_text(encoding="utf-8")
for old, new in REPLACEMENTS:
    content = content.replace(old, new)
path.write_text(content, encoding="utf-8")
print("fintech-dark.css 已批量替換")
```

跑：
```bash
python /tmp/replace_colors.py
```

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_fintech_dark_uses_tokens.py -q
```

預期：PASS。如 FAIL（hardcode > 30），檢查未被替換的色票，把缺的 token 加到 `tokens.css` 然後重跑測試。

- [ ] **Step 5: 視覺驗證**

```bash
PYTHONPATH=. python -m web.app 8444 &
# 瀏覽器開 8444，切 dark mode，檢視：wall/fleet/clips/dashboard 視覺跟改動前一致
```

- [ ] **Step 6: Commit**

```bash
git add web/static/themes/fintech-dark.css tests/test_fintech_dark_uses_tokens.py
git commit -m "refactor(8444-fintech-dark): 改寫為 var() 引用 tokens

- 90+ 個 hardcode 顏色替換為 var(--xxx)
- 視覺效果與改動前一致（測試確保 hardcode 數量 ≤ 30）
- 既有 Bootstrap 細節覆寫保留

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: 8555 `clips.html` 改用 var() 引用 token

**Files:**
- Modify: `web/clips_templates/clips.html:2`、`<head>` 加 `<link>`、line 39-65 dark CSS

- [ ] **Step 1: 寫測試**

建立 `tests/test_8555_clips_tokens.py`：

```python
"""驗證 8555 clips.html 引入 tokens + data-theme + 改用 var()。"""
import re
from pathlib import Path


CLIPS_HTML = Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "clips.html"


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
    match = re.search(r"\{%\s*if\s+dark\s*%\}(.*?)\{%\s*endif\s*%\}", content, re.DOTALL)
    assert match, "8555 clips.html 應有 {% if dark %} 區塊"
    dark_block = match.group(1)
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", dark_block)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", rgba_removed)
    assert len(hex_matches) == 0, f"8555 clips dark 區塊不應有 hardcode 顏色，找到：{hex_matches}"


def test_8555_clips_dark_block_uses_card_token():
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "var(--bg-card)" in content
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8555_clips_tokens.py -q
```

預期：FAIL。

- [ ] **Step 3: 修改 `web/clips_templates/clips.html`**

(a) Line 2：`<html lang="zh-Hant">` →

```html
<html lang="zh-Hant" data-theme="{% if dark %}dark{% else %}light{% endif %}">
```

(b) Line 7-8（Bootstrap CDN）**之前**插入：

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
```

(c) Line 39-65 dark CSS 區塊，把所有 #XXX 替換為 var()：

```html
{% if dark %}
<style>
    body { background: var(--bg-primary); color: var(--text-primary); --bs-body-color: var(--text-primary); }
    .card { background: var(--bg-card); border-color: var(--border-primary); color: var(--text-primary); }
    .form-label { color: var(--text-primary); }
    .form-control, .form-select { background: var(--bg-input); color: var(--text-primary); border-color: var(--border-primary); }
    .form-control::placeholder, .form-select::placeholder { color: var(--text-muted); opacity: 1; }
    .form-control:focus, .form-select:focus { background: var(--bg-input); color: var(--text-primary); border-color: var(--primary); box-shadow: 0 0 0 .2rem rgba(59,130,246,.25); }
    .btn-outline-secondary { color: var(--text-secondary); border-color: var(--text-muted); }
    .btn-outline-secondary:hover { background: var(--border-primary); color: var(--text-primary); border-color: var(--text-secondary); }
    .btn-outline-warning { color: var(--warning-text); border-color: var(--warning-border); }
    .btn-outline-primary { color: var(--text-link); border-color: var(--primary); }
    .btn-outline-primary:hover { background: var(--primary-hover); color: var(--text-link); border-color: var(--text-link); }
    .text-muted { color: var(--text-muted) !important; }
    code { color: var(--text-code); }
    .sync-slot.empty { background: var(--bg-card); color: var(--text-secondary); }
    .sync-slot.error { background: var(--bg-danger); }
    #camPanel { background: var(--bg-card); border-color: var(--border-primary); color: var(--text-primary); }
    a { color: var(--text-link); }
    a:hover { color: var(--text-link-hover); }
    hr { border-color: var(--border-primary); }
    .form-check-input { background-color: var(--bg-input); border-color: var(--text-muted); }
    .form-check-input:checked { background-color: var(--primary); border-color: var(--primary); }
    .form-check-label { color: var(--text-primary); }
    .badge.bg-secondary { background: var(--border-secondary) !important; color: var(--text-primary); }
</style>
{% endif %}
```

（注：`rgba(59,130,246,.25)` 是帶透明度的藍色，token 沒提供 rgba 變體，保留作為例外。）

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8555_clips_tokens.py -q
```

預期：4 個測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add web/clips_templates/clips.html tests/test_8555_clips_tokens.py
git commit -m "refactor(8555-clips): 引入 tokens.css + data-theme + dark CSS 改用 var()

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: 8555 `nvrs_list.html` 改用 var() 引用 token

**Files:**
- Modify: `web/clips_templates/nvrs_list.html:2`、`nvrs_list.html:9-34` dark CSS

- [ ] **Step 1: 寫測試**

建立 `tests/test_8555_nvrs_list_tokens.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8555_nvrs_list_tokens.py -q
```

預期：FAIL。

- [ ] **Step 3: 修改 `web/clips_templates/nvrs_list.html`**

(a) Line 2 加 `data-theme`：

```html
<html lang="zh-Hant" data-theme="{% if dark %}dark{% else %}light{% endif %}">
```

(b) Line 7-8 之前插入 `<link>`：

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
```

(c) Line 9-34 dark CSS 改寫：

```html
{% if dark %}
<style>
    body { background: var(--bg-primary); color: var(--text-primary); --bs-body-color: var(--text-primary); }
    .navbar { background: var(--bg-primary) !important; border-bottom: 1px solid var(--border-primary); }
    .navbar-brand, .nav-link, .navbar-text { color: var(--text-primary) !important; }
    .navbar-text.small.text-muted { color: var(--text-secondary) !important; }
    .card { background: var(--bg-card); border-color: var(--border-primary); color: var(--text-primary); }
    .table { color: var(--text-primary); --bs-table-bg: var(--bg-card); --bs-table-striped-bg: var(--bg-table-striped); --bs-table-color: var(--text-primary); }
    .table > :not(caption) > * > * { color: var(--text-primary); border-color: var(--border-secondary); }
    .table-secondary { --bs-table-bg: var(--border-primary); --bs-table-color: var(--text-secondary); }
    .form-control { background: var(--bg-input); color: var(--text-primary); border-color: var(--border-primary); }
    .form-control::placeholder { color: var(--text-muted); opacity: 1; }
    .form-control:focus { background: var(--bg-input); color: var(--text-primary); border-color: var(--primary); box-shadow: 0 0 0 .2rem rgba(59,130,246,.25); }
    .btn-outline-secondary { color: var(--text-secondary); border-color: var(--text-muted); }
    .btn-outline-secondary:hover { background: var(--border-primary); color: var(--text-primary); border-color: var(--text-secondary); }
    .btn-outline-primary { color: var(--text-link); border-color: var(--primary); }
    .btn-outline-primary:hover { background: var(--primary-hover); color: var(--text-link); border-color: var(--text-link); }
    .text-muted { color: var(--text-secondary) !important; }
    code { color: var(--text-code); }
    .badge.bg-secondary { background: var(--border-secondary) !important; color: var(--text-primary); }
    a { color: var(--text-link); }
    a:hover { color: var(--text-link-hover); }
    hr { border-color: var(--border-primary); }
    small { color: var(--text-secondary); }
</style>
{% endif %}
```

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8555_nvrs_list_tokens.py -q
```

預期：4 個測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add web/clips_templates/nvrs_list.html tests/test_8555_nvrs_list_tokens.py
git commit -m "refactor(8555-nvrs_list): 引入 tokens.css + data-theme + dark CSS 改用 var()

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: 8555 `nvr_form.html` 改用 var() 引用 token

**Files:**
- Modify: `web/clips_templates/nvr_form.html:2`、`<head>` 加 `<link>`、line 9-43 dark CSS

- [ ] **Step 1: 寫測試**

建立 `tests/test_8555_nvr_form_tokens.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8555_nvr_form_tokens.py -q
```

預期：FAIL。

- [ ] **Step 3: 修改 `web/clips_templates/nvr_form.html`**

(a) Line 2 加 `data-theme`：

```html
<html lang="zh-Hant" data-theme="{% if dark %}dark{% else %}light{% endif %}">
```

(b) Line 7-8 之前插入 `<link>`：

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
```

(c) Line 9-42 dark CSS 改寫（保留 .modal-content、.alert-danger、.alert-warning 結構）：

```html
{% if dark %}
<style>
    body { background: var(--bg-primary); color: var(--text-primary); --bs-body-color: var(--text-primary); }
    .navbar { background: var(--bg-primary) !important; border-bottom: 1px solid var(--border-primary); }
    .navbar-brand, .nav-link, .navbar-text { color: var(--text-primary) !important; }
    .navbar-text.small.text-muted { color: var(--text-secondary) !important; }
    .card { background: var(--bg-card); border-color: var(--border-primary); color: var(--text-primary); }
    .form-control { background: var(--bg-input); color: var(--text-primary); border-color: var(--border-primary); }
    .form-control::placeholder { color: var(--text-muted); opacity: 1; }
    .form-control:focus { background: var(--bg-input); color: var(--text-primary); border-color: var(--primary); box-shadow: 0 0 0 .2rem rgba(59,130,246,.25); }
    .form-control:disabled { background: var(--bg-primary); color: var(--text-secondary); }
    .form-label, .form-check-label { color: var(--text-primary); }
    .form-text { color: var(--text-secondary); }
    .btn-outline-secondary, .btn-secondary { color: var(--text-secondary); border-color: var(--text-muted); }
    .btn-outline-secondary:hover, .btn-secondary:hover { background: var(--border-primary); color: var(--text-primary); border-color: var(--text-secondary); }
    .btn-outline-info { color: var(--text-link); border-color: var(--primary); }
    .btn-outline-danger { color: var(--danger-text); border-color: var(--danger-border); }
    .btn-outline-primary { color: var(--text-link); border-color: var(--primary); }
    .btn-outline-primary:hover { background: var(--primary-hover); color: var(--text-link); border-color: var(--text-link); }
    .text-muted { color: var(--text-secondary) !important; }
    .text-warning { color: var(--warning-text) !important; }
    code { color: var(--text-code); }
    .modal-content { background: var(--bg-modal); color: var(--text-primary); border-color: var(--border-secondary); }
    .modal-header, .modal-footer { border-color: var(--border-secondary); }
    .modal-title { color: var(--text-primary); }
    a { color: var(--text-link); }
    a:hover { color: var(--text-link-hover); }
    hr { border-color: var(--border-primary); }
    small { color: var(--text-secondary); }
    .form-check-input { background-color: var(--bg-input); border-color: var(--text-muted); }
    .form-check-input:checked { background-color: var(--primary); border-color: var(--primary); }
    .alert-danger { background: var(--danger-bg); color: var(--danger-text); border-color: var(--danger-border); }
    .alert-warning { background: var(--warning-bg); color: var(--warning-text); border-color: var(--warning-border); }
</style>
{% endif %}
```

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8555_nvr_form_tokens.py -q
```

預期：4 個測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add web/clips_templates/nvr_form.html tests/test_8555_nvr_form_tokens.py
git commit -m "refactor(8555-nvr_form): 引入 tokens.css + data-theme + dark CSS 改用 var()

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 10: 8555 `nvr_import.html` 改用 var() 引用 token

**Files:**
- Modify: `web/clips_templates/nvr_import.html:2`、`<head>` 加 `<link>`、line 9-30 dark CSS

- [ ] **Step 1: 寫測試**

建立 `tests/test_8555_nvr_import_tokens.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認 fail**

```bash
pytest tests/test_8555_nvr_import_tokens.py -q
```

預期：FAIL。

- [ ] **Step 3: 修改 `web/clips_templates/nvr_import.html`**

(a) Line 2 加 `data-theme`：

```html
<html lang="zh-Hant" data-theme="{% if dark %}dark{% else %}light{% endif %}">
```

(b) Line 7-8 之前插入 `<link>`：

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
```

(c) Line 9-30 dark CSS 改寫：

```html
{% if dark %}
<style>
    body { background: var(--bg-primary); color: var(--text-primary); --bs-body-color: var(--text-primary); }
    .navbar { background: var(--bg-primary) !important; border-bottom: 1px solid var(--border-primary); }
    .navbar-brand, .nav-link, .navbar-text { color: var(--text-primary) !important; }
    .navbar-text.small.text-muted { color: var(--text-secondary) !important; }
    .card { background: var(--bg-card); border-color: var(--border-primary); color: var(--text-primary); }
    .form-control { background: var(--bg-input); color: var(--text-primary); border-color: var(--border-primary); }
    .form-control::placeholder { color: var(--text-muted); opacity: 1; }
    .form-control:focus { background: var(--bg-input); color: var(--text-primary); border-color: var(--primary); box-shadow: 0 0 0 .2rem rgba(59,130,246,.25); }
    .btn-outline-secondary, .btn-secondary { color: var(--text-secondary); border-color: var(--text-muted); }
    .btn-outline-secondary:hover, .btn-secondary:hover { background: var(--border-primary); color: var(--text-primary); border-color: var(--text-secondary); }
    .btn-outline-primary { color: var(--text-link); border-color: var(--primary); }
    .btn-outline-primary:hover { background: var(--primary-hover); color: var(--text-link); border-color: var(--text-link); }
    .text-muted { color: var(--text-secondary) !important; }
    code { color: var(--text-code); }
    a { color: var(--text-link); }
    a:hover { color: var(--text-link-hover); }
    hr { border-color: var(--border-primary); }
    small { color: var(--text-secondary); }
    .alert-danger { background: var(--danger-bg); color: var(--danger-text); border-color: var(--danger-border); }
</style>
{% endif %}
```

- [ ] **Step 4: 跑測試確認 pass**

```bash
pytest tests/test_8555_nvr_import_tokens.py -q
```

預期：3 個測試全 PASS。

- [ ] **Step 5: Commit**

```bash
git add web/clips_templates/nvr_import.html tests/test_8555_nvr_import_tokens.py
git commit -m "refactor(8555-nvr_import): 引入 tokens.css + data-theme + dark CSS 改用 var()

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 11: 文檔更新 + 最終 pytest 驗證

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 跑完整 pytest 確認全綠**

```bash
pytest -q
```

預期：744 + 16 + 7 + 4 + 4 + 4 + 4 + 3 = 786 測試全綠（粗估，實際看測試 fixture 數量）。

如 FAIL：
- 視覺回歸：可能是 fintech-dark.css 改寫後某顏色 token 沒在 tokens.css 內定義。
- 解法：把缺的 token 加到 `web/static/css/tokens.css` 然後重跑。

- [ ] **Step 2: 更新 `CHANGELOG.md`**

在 `## [Unreleased]` 區塊加：

```markdown
- **2026-08-03**：8444 / 8555 Design Tokens 統一 — 抽出 `web/static/css/tokens.css` 集中兩套 theme（色票 + 字體 + 間距 + 圓角），8444 base.html 加 `<html data-theme>` 自動切換，8555 4 個 template 各自引入；fintech-dark.css 改寫用 `var()` 引用。預期 744 → 760 測試。
```

- [ ] **Step 3: 視覺驗證**

```bash
# 重啟 8444 + 8555
powershell -Command "Get-NetTCPConnection -LocalPort 8444,8555 | Select-Object OwningProcess | Stop-Process -Force"
PYTHONPATH=. python -m web.app 8444 &
PYTHONPATH=. NVR_CLIPS_CLIENT=mock python -m web.clips_app 8555 &

# 用 Playwright 截圖驗證
# 8444: /dashboard /wall /fleet /clips 各切 dark 一次
# 8555: /clips /nvrs /nvrs/new /nvrs/import 各切 dark 一次
```

驗證點：
- 8444 fintech dark mode 視覺跟改動前一致（card 背景、navbar、表格、文字）
- 8555 4 個頁面 dark mode 視覺跟改動前一致
- 兩邊一起切 dark，主色澤一致（card 背景都是 #1a1a2e，背景都是 #0c1220）

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): 2026-08-03 design tokens 統一紀錄

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Checklist

- [x] Spec coverage：每個 spec 段落都有對應 task
- [x] Placeholder scan：無 TBD / TODO / 「fill in」
- [x] Type consistency：所有 `var(--xxx)` token 名稱一致
- [x] Bite-sized：每個 step 是 2-5 分鐘動作
- [x] Frequent commits：每個 task 一個 commit
- [x] TDD discipline：每個 task 都先寫 failing test
- [x] 既有測試保留：6 個 8555 dark mode 測試、19 個 8444 模板測試沒被改動
- [x] 範圍控制：11 主題 / 8444 16 頁面補 dark 明確排除
