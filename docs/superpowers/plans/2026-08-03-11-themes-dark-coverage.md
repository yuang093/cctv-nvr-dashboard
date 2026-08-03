# Spec E：11 主題 dark 補齊 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為 8444 的 12 個 light theme 各加一個 `*-dark.css`（fintech-dark 已存在），dark 模式時保留各 theme 自己的品牌色（accent）。

**Architecture:** fintech-dark.css 提供 90% token 覆寫（底色）。每個新 `*-dark.css` 只覆寫 4 個 accent token（--primary / --primary-hover / --text-link / --text-link-hover）。base.html 載入順序：tokens.css → bootstrap → style.css → fintech-dark.css → *-dark.css（最後，accent 優先級最高）。

**Tech Stack:** Python 3.10+，Flask，Jinja2，純 CSS（data-theme="dark" 切換），pytest

---

## Accent 色票總表（所有 12 個 *-dark.css 統一用這個）

從各 light theme 抽出的 accent（保留品牌色）：

| Theme | --primary | --primary-hover | --text-link | --text-link-hover |
|---|---|---|---|---|
| brutal | `#ffff00` | `#facc15` | `#facc15` | `#fde047` |
| cyberpunk | `#00d2f7` | `#00ff9d` | `#00d2f7` | `#00ff9d` |
| earthy | `#87a878` | `#4a6741` | `#4a6741` | `#87a878` |
| editorial | `#525252` | `#1a1a1a` | `#1a1a1a` | `#525252` |
| eink | `#ffffff` | `#cccccc` | `#ffffff` | `#cccccc` |
| enterprise | `#3b82f6` | `#60a5fa` | `#3b82f6` | `#60a5fa` |
| glass | `#a78bfa` | `#c4b5fd` | `#a78bfa` | `#c4b5fd` |
| gradient | `#8b5cf6` | `#3b82f6` | `#8b5cf6` | `#3b82f6` |
| minimal | `#6b7280` | `#111827` | `#6b7280` | `#111827` |
| nordic | `#1e40af` | `#3b82f6` | `#1e40af` | `#3b82f6` |
| terminal | `#79c0ff` | `#388bfd` | `#79c0ff` | `#388bfd` |

fintech-dark.css 已有（Spec D 完成），**跳過**。

---

## Task 1：建立 brutal-dark.css + 測試

**Files:**
- Create: `web/static/themes/brutal-dark.css`
- Test: `tests/test_themes_dark_brutal.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 brutal-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "brutal-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists(), f"brutal-dark.css 應存在於 {DARK_CSS}"


def test_dark_has_data_theme_block():
    """必須用 [data-theme="dark"] 觸發。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content), \
        '應有 [data-theme="dark"] { ... } 區塊'


def test_dark_overrides_accent_tokens():
    """至少覆寫 --primary / --primary-hover / --text-link / --text-link-hover 4 個 accent。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content, f"brutal-dark.css 應覆寫 {token}"


def test_dark_accent_is_brutal_yellow():
    """brutal 品牌色應為亮黃（保留 light theme 特色）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    # 至少 --primary 應為亮黃系
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match, "--primary 應定義"
    color = primary_match.group(1).lower()
    # 接受 #ffff00 / #facc15 等黃系
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    assert r > 200 and g > 180 and b < 100, f"brutal --primary 應為亮黃系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_brutal.py -v`
Expected: FAIL "brutal-dark.css 應存在於 ..."

- [ ] **Step 3: Write minimal implementation**

```css
/* brutal-dark.css — 2026-08-03 Spec E */
/* brutal 品牌色：亮黃系（從 light theme 抽出） */
/* 底色由 fintech-dark.css 提供 */

[data-theme="dark"] {
    --primary: #ffff00;
    --primary-hover: #facc15;
    --text-link: #facc15;
    --text-link-hover: #fde047;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_brutal.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/brutal-dark.css tests/test_themes_dark_brutal.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): brutal-dark + 4 tests (Spec E Task 1)"
```

---

## Task 2：建立 cyberpunk-dark.css + 測試

**Files:**
- Create: `web/static/themes/cyberpunk-dark.css`
- Test: `tests/test_themes_dark_cyberpunk.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 cyberpunk-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "cyberpunk-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_cyan():
    """cyberpunk 品牌色應為青色系（#00d2f7 / #00ff9d）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # 青色系：b > 200, g > 200, r < 100
    assert r < 100 and g > 150 and b > 150, f"cyberpunk --primary 應為青色系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_cyberpunk.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* cyberpunk-dark.css — 2026-08-03 Spec E */
/* cyberpunk 品牌色：青色 + 螢光綠（從 light theme 抽出） */

[data-theme="dark"] {
    --primary: #00d2f7;
    --primary-hover: #00ff9d;
    --text-link: #00d2f7;
    --text-link-hover: #00ff9d;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_cyberpunk.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/cyberpunk-dark.css tests/test_themes_dark_cyberpunk.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): cyberpunk-dark + 4 tests (Spec E Task 2)"
```

---

## Task 3：建立 earthy-dark.css + 測試

**Files:**
- Create: `web/static/themes/earthy-dark.css`
- Test: `tests/test_themes_dark_earthy.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 earthy-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "earthy-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_forest_green():
    """earthy 品牌色應為森林綠系。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # 綠系：g > r, g > b
    assert g > r and g > b, f"earthy --primary 應為綠系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_earthy.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* earthy-dark.css — 2026-08-03 Spec E */
/* earthy 品牌色：森林綠（從 light theme 抽出） */

[data-theme="dark"] {
    --primary: #87a878;
    --primary-hover: #4a6741;
    --text-link: #4a6741;
    --text-link-hover: #87a878;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_earthy.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/earthy-dark.css tests/test_themes_dark_earthy.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): earthy-dark + 4 tests (Spec E Task 3)"
```

---

## Task 4：建立 editorial-dark.css + 測試

**Files:**
- Create: `web/static/themes/editorial-dark.css`
- Test: `tests/test_themes_dark_editorial.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 editorial-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "editorial-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_neutral_gray():
    """editorial 品牌色應為黑/灰系（編輯簡潔感）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # 中性灰：r ≈ g ≈ b
    assert abs(r - g) < 20 and abs(g - b) < 20, f"editorial --primary 應為中性灰，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_editorial.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* editorial-dark.css — 2026-08-03 Spec E */
/* editorial 品牌色：黑/灰（編輯簡潔感） */

[data-theme="dark"] {
    --primary: #525252;
    --primary-hover: #1a1a1a;
    --text-link: #1a1a1a;
    --text-link-hover: #525252;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_editorial.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/editorial-dark.css tests/test_themes_dark_editorial.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): editorial-dark + 4 tests (Spec E Task 4)"
```

---

## Task 5：建立 eink-dark.css + 測試

**Files:**
- Create: `web/static/themes/eink-dark.css`
- Test: `tests/test_themes_dark_eink.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 eink-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "eink-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_white():
    """eink 品牌色應為純白（e-ink 黑白感）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    assert r > 230 and g > 230 and b > 230, f"eink --primary 應為近白色，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_eink.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* eink-dark.css — 2026-08-03 Spec E */
/* eink 品牌色：純白（e-ink 黑白感） */

[data-theme="dark"] {
    --primary: #ffffff;
    --primary-hover: #cccccc;
    --text-link: #ffffff;
    --text-link-hover: #cccccc;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_eink.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/eink-dark.css tests/test_themes_dark_eink.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): eink-dark + 4 tests (Spec E Task 5)"
```

---

## Task 6：建立 enterprise-dark.css + 測試

**Files:**
- Create: `web/static/themes/enterprise-dark.css`
- Test: `tests/test_themes_dark_enterprise.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 enterprise-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "enterprise-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_blue():
    """enterprise 品牌色應為藍系（企業感）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    b = int(color[5:7], 16)
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    assert b > 200 and b > r and b > g, f"enterprise --primary 應為藍系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_enterprise.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* enterprise-dark.css — 2026-08-03 Spec E */
/* enterprise 品牌色：企業藍（從 light theme 抽出） */

[data-theme="dark"] {
    --primary: #3b82f6;
    --primary-hover: #60a5fa;
    --text-link: #3b82f6;
    --text-link-hover: #60a5fa;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_enterprise.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/enterprise-dark.css tests/test_themes_dark_enterprise.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): enterprise-dark + 4 tests (Spec E Task 6)"
```

---

## Task 7：建立 glass-dark.css + 測試

**Files:**
- Create: `web/static/themes/glass-dark.css`
- Test: `tests/test_themes_dark_glass.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 glass-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "glass-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_purple():
    """glass 品牌色應為紫系（#a78bfa / #6366f1）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # 紫色：r > g, b > g
    assert r > g and b > g, f"glass --primary 應為紫系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_glass.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* glass-dark.css — 2026-08-03 Spec E */
/* glass 品牌色：紫（玻璃透明感） */

[data-theme="dark"] {
    --primary: #a78bfa;
    --primary-hover: #c4b5fd;
    --text-link: #a78bfa;
    --text-link-hover: #c4b5fd;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_glass.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/glass-dark.css tests/test_themes_dark_glass.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): glass-dark + 4 tests (Spec E Task 7)"
```

---

## Task 8：建立 gradient-dark.css + 測試

**Files:**
- Create: `web/static/themes/gradient-dark.css`
- Test: `tests/test_themes_dark_gradient.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 gradient-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "gradient-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_purple_or_blue():
    """gradient 品牌色：紫→藍漸層。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    # 紫（#8b5cf6）或 藍（#3b82f6）皆可
    is_purple = r > g and b > g
    is_blue = b > 200 and b > r and b > g
    assert is_purple or is_blue, f"gradient --primary 應為紫或藍系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_gradient.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* gradient-dark.css — 2026-08-03 Spec E */
/* gradient 品牌色：紫→藍漸層 */

[data-theme="dark"] {
    --primary: #8b5cf6;
    --primary-hover: #3b82f6;
    --text-link: #8b5cf6;
    --text-link-hover: #3b82f6;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_gradient.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/gradient-dark.css tests/test_themes_dark_gradient.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): gradient-dark + 4 tests (Spec E Task 8)"
```

---

## Task 9：建立 minimal-dark.css + 測試

**Files:**
- Create: `web/static/themes/minimal-dark.css`
- Test: `tests/test_themes_dark_minimal.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 minimal-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "minimal-dark.css"


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
    """minimal 品牌色應為灰系（簡約感）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    assert abs(r - g) < 20 and abs(g - b) < 20, f"minimal --primary 應為中性灰，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_minimal.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* minimal-dark.css — 2026-08-03 Spec E */
/* minimal 品牌色：灰（簡約感） */

[data-theme="dark"] {
    --primary: #6b7280;
    --primary-hover: #111827;
    --text-link: #6b7280;
    --text-link-hover: #111827;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_minimal.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/minimal-dark.css tests/test_themes_dark_minimal.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): minimal-dark + 4 tests (Spec E Task 9)"
```

---

## Task 10：建立 nordic-dark.css + 測試

**Files:**
- Create: `web/static/themes/nordic-dark.css`
- Test: `tests/test_themes_dark_nordic.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 nordic-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "nordic-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_nordic_blue():
    """nordic 品牌色應為北歐深藍。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    b = int(color[5:7], 16)
    r = int(color[1:3], 16)
    assert b > 150 and b > r, f"nordic --primary 應為藍系，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_nordic.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* nordic-dark.css — 2026-08-03 Spec E */
/* nordic 品牌色：北歐深藍 */

[data-theme="dark"] {
    --primary: #1e40af;
    --primary-hover: #3b82f6;
    --text-link: #1e40af;
    --text-link-hover: #3b82f6;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_nordic.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/nordic-dark.css tests/test_themes_dark_nordic.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): nordic-dark + 4 tests (Spec E Task 10)"
```

---

## Task 11：建立 terminal-dark.css + 測試

**Files:**
- Create: `web/static/themes/terminal-dark.css`
- Test: `tests/test_themes_dark_terminal.py`

- [ ] **Step 1: Write the failing test**

```python
"""驗證 terminal-dark.css 內容齊全。"""
import re
from pathlib import Path

import pytest

DARK_CSS = Path(__file__).resolve().parent.parent / "web" / "static" / "themes" / "terminal-dark.css"


def test_dark_file_exists():
    assert DARK_CSS.exists()


def test_dark_has_data_theme_block():
    content = DARK_CSS.read_text(encoding="utf-8")
    assert re.search(r'\[data-theme=["\']dark["\']\]\s*\{', content)


def test_dark_overrides_accent_tokens():
    content = DARK_CSS.read_text(encoding="utf-8")
    for token in ["--primary", "--primary-hover", "--text-link", "--text-link-hover"]:
        assert token in content


def test_dark_accent_is_terminal_blue():
    """terminal 品牌色：終端機藍（#79c0ff / #388bfd）。"""
    content = DARK_CSS.read_text(encoding="utf-8")
    primary_match = re.search(r"--primary:\s*(#[0-9a-fA-F]+)", content)
    assert primary_match
    color = primary_match.group(1).lower()
    b = int(color[5:7], 16)
    g = int(color[3:5], 16)
    r = int(color[1:3], 16)
    # 終端藍：b 高，g 中，r 低
    assert b > 200 and g > 150 and r < 200, f"terminal --primary 應為終端藍，實際 {color}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_terminal.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```css
/* terminal-dark.css — 2026-08-03 Spec E */
/* terminal 品牌色：終端機藍（github dark 配色） */

[data-theme="dark"] {
    --primary: #79c0ff;
    --primary-hover: #388bfd;
    --text-link: #79c0ff;
    --text-link-hover: #388bfd;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_themes_dark_terminal.py -v`
Expected: PASS 4/4

- [ ] **Step 5: Commit**

```bash
git add web/static/themes/terminal-dark.css tests/test_themes_dark_terminal.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(themes): terminal-dark + 4 tests (Spec E Task 11)"
```

---

## Task 12：base.html 載入 12 個 *-dark.css + 最終測試

**Files:**
- Modify: `web/templates/base.html:13-43`
- Test: `tests/test_base_dark_theme_links.py`（驗證 12 個 link 都存在）

- [ ] **Step 1: Write the failing test**

```python
"""驗證 base.html 在 dark 模式時為 12 個 theme 各載入對應 *-dark.css。"""
import re
from pathlib import Path

import pytest

BASE_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"

# 12 個需要 dark CSS 的 theme（fintech-dark 已存在）
DARK_THEMES = [
    "brutal", "cyberpunk", "earthy", "editorial", "eink", "enterprise",
    "glass", "gradient", "minimal", "nordic", "terminal", "fintech",
]


def test_base_html_exists():
    assert BASE_HTML.exists()


def test_base_html_loads_all_dark_themes():
    """base.html 應為 12 個 theme 各載入對應 *-dark.css。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    for theme in DARK_THEMES:
        # 期待 'theme == "X" and dark' 模式
        pattern = rf'theme\s*==\s*[\'\"]{theme}[\\'\"]\s+and\s+dark'
        assert re.search(pattern, content), f"base.html 應有 theme == '{theme}' and dark 區塊"
        # 期待 'themes/{theme}-dark.css' link
        link_pattern = rf'themes/{theme}-dark\.css'
        assert re.search(link_pattern, content), f"base.html 應 link themes/{theme}-dark.css"


def test_dark_link_loaded_after_fintech_dark():
    """*-dark.css 必須在 fintech-dark.css 之後載入（保證 accent 覆寫最高優先級）。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    fintech_dark_pos = content.find("fintech-dark.css")
    assert fintech_dark_pos > 0, "fintech-dark.css 應存在"
    # 找最晚出現的 *-dark.css link
    last_dark_pos = max(
        content.find(f"{t}-dark.css") for t in DARK_THEMES if f"{t}-dark.css" in content
    )
    assert last_dark_pos > fintech_dark_pos, "所有 *-dark.css 應在 fintech-dark.css 之後"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_base_dark_theme_links.py -v`
Expected: 至少 6 個 FAIL（既有 base.html 只有 1 個 fintech-dark link）

- [ ] **Step 3: Write minimal implementation**

修改 `web/templates/base.html`，在 line 13（fintech-dark.css link 之後）插入 12 個 `{% if theme == 'X' and dark %}` 區塊。

完整修改（替換 line 13-43 的 theme link 區塊）：

```html
{# 11 主題的 light 版本（fintech 已是 dark，fintech-dark.css 在下方）#}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/fintech-dark.css') }}">

{% if theme == 'nordic' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/nordic.css') }}">
{% elif theme == 'brutal' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/brutal.css') }}">
{% elif theme == 'fintech' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/fintech.css') }}">
{% elif theme == 'earthy' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/earthy.css') }}">
{% elif theme == 'editorial' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/editorial.css') }}">
{% elif theme == 'eink' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/eink.css') }}">
{% elif theme == 'enterprise' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/enterprise.css') }}">
{% elif theme == 'glass' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/glass.css') }}">
{% elif theme == 'gradient' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/gradient.css') }}">
{% elif theme == 'minimal' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/minimal.css') }}">
{% elif theme == 'cyberpunk' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/cyberpunk.css') }}">
{% elif theme == 'terminal' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/terminal.css') }}">
{% endif %}

{# 11 主題的 dark 版本（accent 覆寫，必須在 fintech-dark.css 之後）#}
{% if dark and theme == 'nordic' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/nordic-dark.css') }}">
{% elif dark and theme == 'brutal' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/brutal-dark.css') }}">
{% elif dark and theme == 'fintech' %}
{# fintech-dark.css 已載入，跳過 #}
{% elif dark and theme == 'earthy' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/earthy-dark.css') }}">
{% elif dark and theme == 'editorial' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/editorial-dark.css') }}">
{% elif dark and theme == 'eink' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/eink-dark.css') }}">
{% elif dark and theme == 'enterprise' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/enterprise-dark.css') }}">
{% elif dark and theme == 'glass' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/glass-dark.css') }}">
{% elif dark and theme == 'gradient' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/gradient-dark.css') }}">
{% elif dark and theme == 'minimal' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/minimal-dark.css') }}">
{% elif dark and theme == 'cyberpunk' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/cyberpunk-dark.css') }}">
{% elif dark and theme == 'terminal' %}
<link rel="stylesheet" href="{{ url_for('static', filename='themes/terminal-dark.css') }}">
{% endif %}
```

注意：原 base.html 在 cyberpunk 之後還有幾個 theme（如有），請對照原檔案結構插入，不要刪除既有 theme。

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_base_dark_theme_links.py -v`
Expected: PASS 3/3

- [ ] **Step 5: 跑全部測試確認沒壞**

Run: `PYTHONIOENCODING=utf-8 python -m pytest -q`
Expected: PASS 全綠（預期 805 → 853，+48：12 個 theme × 4 個 test = 48）

- [ ] **Step 6: Commit**

```bash
git add web/templates/base.html tests/test_base_dark_theme_links.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(base): load 12 *-dark.css for 11 themes (Spec E Task 12)

為 12 個 light theme 各加 dark 變體 link，accent 覆寫在 fintech-dark.css
之後載入以保最高優先級。fintech-dark.css 既有，跳過重複。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 13：CHANGELOG + 最終驗證

**Files:**
- Modify: `CHANGELOG.md`
- Test: 跑全部 pytest 確認 853 全綠

- [ ] **Step 1: 更新 CHANGELOG.md**

在 `[Unreleased] ### Added` 段加：

```markdown
- **2026-08-03**：11 主題 dark 補齊（Spec E，12 commits，48 新測試）—— 為 brutal / cyberpunk / earthy / editorial / eink / enterprise / glass / gradient / minimal / nordic / terminal / fintech 各加 `*-dark.css`（fintech-dark 既有），每個保留 light theme 品牌色作 accent。base.html 加 12 個 `{% if theme == 'X' and dark %}` link，`*-dark.css` 在 fintech-dark.css 之後載入保最高優先級。Spec 在 `docs/superpowers/specs/2026-08-03-11-themes-dark-coverage.md`，計畫在 `docs/superpowers/plans/2026-08-03-11-themes-dark-coverage.md`。預期 805 → 853 測試（實際待確認）。
```

- [ ] **Step 2: 跑全部測試**

Run: `PYTHONIOENCODING=utf-8 python -m pytest -q`
Expected: PASS（853 測試）

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "docs(changelog): Spec E 11 主題 dark 補齊紀錄 (Task 13)"
```

---

## 驗證總結

### 自動驗證
- 13 個 task，每個都有獨立 test
- pytest 預期 805 → 853（+48：12 個 theme × 4 個 test）
- 所有 commit 都有 `Co-Authored-By: Claude`

### 手動驗證（user 跑）
- 重啟 8444 後切到 dark 模式
- 切換每個 theme，確認 link / 按鈕顏色跟 light theme 品牌色一致
- 13 個 theme 截圖比較

### 不做的事（scope 控制）
- ❌ 不改 8444 16 個 template 內的 inline dark CSS
- ❌ 不重做 fintech-dark.css（Spec D 完成）
- ❌ 不改 tokens.css
- ❌ 不動 8555
- ❌ 不重新設計各 theme 品牌色
