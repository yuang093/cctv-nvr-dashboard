# Spec E：11 主題 dark 補齊（8444 完整 dark mode 覆蓋）

> **日期**：2026-08-03
> **作者**：Claude（subagent-driven）
> **狀態**：🟡 Draft → 🟢 Approved
> **從屬**：Spec D（design tokens 統一）的延伸

---

## Context

Spec D（2026-08-03）已將 8444 抽到 `tokens.css` 集中色票，並把唯一有 dark 版本的 `fintech-dark.css` 改用 `var(--xxx)` 引用。但 8444 還有 **12 個其他 theme**（brutal / cyberpunk / earthy / editorial / eink / enterprise / fintech / glass / gradient / minimal / nordic / terminal）**只有 light 版本**，切到 dark 模式時仍用 fintech-dark 的顏色。

**目標**：13 個 theme 全部支援 dark mode，**保留各 theme 自己的品牌色**（accent）。

---

## Goal

- ✅ 13 個 theme 全部都有對應的 `*-dark.css`
- ✅ 每個 dark 變體保留該 theme 的品牌色（accent / link / hover）
- ✅ 共用 `fintech-dark.css` 的底色（90% 顏色），只覆寫 accent
- ✅ 不破壞 Spec D 的 `var(--xxx)` 統一架構

---

## Architecture

### CSS 載入順序（`web/templates/base.html` 13 個 theme 各一組）

```html
<!-- 1. tokens.css（31 個淺+深 token） -->
<link rel="stylesheet" href=".../css/tokens.css">

<!-- 2. bootstrap CDN -->
<link rel="stylesheet" href=".../bootstrap.min.css">

<!-- 3. style.css（基礎樣式） -->
<link rel="stylesheet" href=".../style.css">

<!-- 4. fintech-dark.css（dark 底色：90% token 覆寫，永遠載入） -->
<link rel="stylesheet" href=".../themes/fintech-dark.css">

<!-- 5. *-dark.css（accent 覆寫；最後載入以保最高優先級） -->
{% if theme == 'nordic' and dark %}
  <link rel="stylesheet" href=".../themes/nordic-dark.css">
{% elif theme == 'brutal' and dark %}
  ...
```

### 每個 `*-dark.css` 結構

```css
/* 主題 dark 變體 — 只覆寫 accent，底色沿用 fintech-dark.css */
[data-theme="dark"] {
    --primary: <theme accent>;          /* 主色 */
    --primary-hover: <accent darker>;   /* hover 色 */
    --text-link: <accent>;
    --text-link-hover: <accent darker>;
    /* 視需要加 1-3 個 accent 變數覆寫 */
}
```

### Accent 抽取（從各 light theme）

每個 light theme 已有自己的 `:root { --primary / --accent / ... }`。`*-dark.css` 從中抽出 4 個變數覆寫即可。

---

## Scope

### ✅ 做（In Scope）

- 12 個新 `*-dark.css` 檔案（fintech-dark 已存在，跳過）：
  1. `brutal-dark.css`
  2. `cyberpunk-dark.css`
  3. `earthy-dark.css`
  4. `editorial-dark.css`
  5. `eink-dark.css`
  6. `enterprise-dark.css`
  7. `fintech-dark.css` ← **已存在**，不重做
  8. `glass-dark.css`
  9. `gradient-dark.css`
  10. `minimal-dark.css`
  11. `nordic-dark.css`
  12. `terminal-dark.css`

  （金融科技 fintech 是 light，fintech-dark 才是 dark；light 數量 12 + dark 數量 1 = 13 theme，dark 補齊後也 13 個 dark CSS）

- 修改 `web/templates/base.html` 加 12 個新 `{% if theme == 'X' and dark %}` 區塊
- 12 個新測試（每個 *-dark.css 至少 1 個）

### ❌ 不做（Out of Scope）

- 不重做 `fintech-dark.css`（Spec D 已完成 152 → 0 hardcode）
- 不改 8444 16 個 template 內的 inline dark CSS（Spec D 已用 `var()` 引用，自動繼承）
- 不動 8555（clips app 已用統一 tokens + 獨立 dark）
- 不改 `tokens.css`（31 個 token 淺深都已有定義）
- 不重新設計各 theme 品牌色（從原 light theme 直接抽）

---

## File Changes

### 新增（12 個）

```
web/static/themes/
  brutal-dark.css
  cyberpunk-dark.css
  earthy-dark.css
  editorial-dark.css
  eink-dark.css
  enterprise-dark.css
  glass-dark.css
  gradient-dark.css
  minimal-dark.css
  nordic-dark.css
  terminal-dark.css
```

### 修改（1 個）

```
web/templates/base.html（line 13 後追加 12 個 {% if theme == 'X' and dark %}）
```

### 新增測試（12 個）

```
tests/
  test_themes_dark_brutal.py
  test_themes_dark_cyberpunk.py
  test_themes_dark_earthy.py
  test_themes_dark_editorial.py
  test_themes_dark_eink.py
  test_themes_dark_enterprise.py
  test_themes_dark_glass.py
  test_themes_dark_gradient.py
  test_themes_dark_minimal.py
  test_themes_dark_nordic.py
  test_themes_dark_terminal.py
```

每個 test 驗證：
- 檔案存在
- 包含 `[data-theme="dark"]` 區塊
- 至少有 `--primary` 覆寫
- 至少覆寫 2 個 accent 變數

---

## Verification

### 自動驗證
```bash
pytest -q
# 預期 805 → ~817 (+12 個新 theme test)
```

### 視覺驗證（Playwright）
```python
# 對每個 theme + dark 模式截圖，確認 accent 顏色正確
for theme in 13_themes:
    page.goto(f'http://127.0.0.1:8444/?theme={theme}&dark=1')
    page.screenshot(f'theme_{theme}_dark.png')
```

### 手動驗證
- 切換每個 theme 到 dark 模式
- 確認 nav bar / link / 按鈕 hover 都用該 theme accent
- 確認背景仍用 fintech-dark 統一底色

---

## Risks

| 風險 | 緩解 |
|---|---|
| Accent 顏色太亮在深色底上看不清 | 從 light theme 抽 → 自調飽和度/明度，**WCAG AA 4.5:1** 對比 |
| 12 個檔案重複代碼多 | YAGNI 接受 — 每個 5-10 行就夠，總計 ~100 行 |
| Spec D 的 var() 引用斷裂 | 每個 *-dark.css 只覆寫 4 個變數，其他不動 |
| base.html link 順序錯 | spec 明確規定順序；subagent 嚴格遵守 |

---

## Success Criteria

- [x] 12 個 *-dark.css 檔案存在且有 [data-theme="dark"] 區塊
- [x] 每個 dark 變體有至少 4 個 accent 變數覆寫
- [x] base.html 13 個 theme + dark 都有對應 link
- [x] 12 個新測試全綠
- [x] 13 個 theme dark 模式視覺截圖確認 accent 正確
- [x] 既有 805 個測試不退步
