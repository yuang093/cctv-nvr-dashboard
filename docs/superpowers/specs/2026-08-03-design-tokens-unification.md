# 8444 / 8555 Design Tokens 統一設計

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽出共用 CSS 設計變數（色票 + 字體 + 間距 + 圓角），8444 跟 8555 兩邊統一引用，達成「同設計語言」效果。

**Architecture:** 單一 `web/static/css/tokens.css` 集中兩套 theme（`:root` 為淺色、`[data-theme="dark"]` 為深色）。8444 跟 8555 各自用 Flask template `<link>` 引入，inline `<style>` 改用 `var()` 引用。Flask session 的 `dark` 旗標決定哪套主題生效。

**Tech Stack:** Python 3.10+ / Flask 3 / CSS 變數（無 Sass / PostCSS 編譯）

---

## 1. Context

### 1.1 現狀整理

**8444（web/app.py / web/templates/）**：
- 已有 dark mode（POST /dark/toggle + Flask session + context_processor 注入 `{{ dark }}`）
- 11 個 light 主題（fintech / nordic / brutal / earthy / editorial / eink / cyberpunk / glass / terminal / minimal / gradient）
- 僅 fintech 主題有 dark 版本（`fintech-dark.css`）
- 19 個 templates 中僅 3 個（wall/fleet/clips）有 inline dark styles
- 其他 16 個頁面只靠 `fintech-dark.css` 全域覆寫
- Spec 來源：2026-08-03 brainstorming session

**8555（web/clips_app.py / web/clips_templates/）**：
- 2026-07-30 8555 plan 完成 dark mode（commit 258e3f1）
- 4 個 template（clips.html / nvrs_list.html / nvr_form.html / nvr_import.html）每個獨立 HTML + Bootstrap CDN
- 4 個 template 各自 inline dark CSS（hardcode 顏色值如 `#0c1220`、`#1a1a2e`）
- 獨立 Flask app，獨立 SECRET_KEY（`NVR_CLIPS_SECRET_KEY`）
- 設計目標：未來**獨立打包**（pyinstaller 各自產 exe）

### 1.2 撞到的問題

- 8444 跟 8555 兩邊都有 dark mode，但**色票不完全一致**（card 背景：8444 `#111827` vs 8555 `#1a1a2e`）
- 兩邊各自 inline 重複相同的色票，新增 dark 色彩時兩處都改
- 缺乏「設計系統」概念，每一次新頁面都 hardcode 顏色

### 1.3 範圍決策（2026-08-03 brainstorming user 確認）

| 決策 | 選擇 | 理由 |
|---|---|---|
| 8444 頁面補齊範圍 | **只抽 token，不補 16 個頁面** | fintech-dark.css 全域覆寫足夠，補頁面是「錦上添花」 |
| Token 範圍 | **色票 + 字體 + 間距**（含圓角） | 涵蓋 dark/light 跨主題 90% 需求，達到「同設計語言」效果 |
| 檔案結構 | **單一 `tokens.css`** | 兩邊用 `<link>` 引入；最低維護成本 |
| 11 主題 dark 補齊 | **不處理** | 維持現狀（僅 fintech 有 dark） |
| 8555 clips.html 重構 | **不處理** | template 重構超出「統一主題」範圍 |

---

## 2. 目標

### 2.1 主要 Goal

抽出單一 `web/static/css/tokens.css`，包含：
- **色票（dark + light）**：背景、文字、邊框、primary、danger、warning 等
- **字體**：font-family（base + mono）、font-size 階梯
- **間距**：spacing 階梯（4px 進位）
- **圓角**：radius 階梯

8444 跟 8555 的 inline dark CSS 全部改用 `var(--xxx)` 引用。

### 2.2 成功標準

- [ ] `web/static/css/tokens.css` 存在
- [ ] 8444 19 個 templates 中，所有 inline `<style>` 區塊（含 wall/fleet/clips）**都不再有 hardcode 顏色字串**（`#XXX` 或 `rgb(...)`）
- [ ] 8555 4 個 templates，所有 inline dark CSS 都不再有 hardcode 顏色字串
- [ ] 8444 的 `fintech-dark.css` 改寫為使用 `var(--xxx)`
- [ ] 8444 跟 8555 切到 dark mode 時，視覺效果**跟改動前一致**（不引入新 bug）
- [ ] 8444 切到 light mode 視覺效果不變
- [ ] pytest 維持 744/744 綠
- [ ] 新增 token 變數測試（顏色值/字體/間距正確輸出）

### 2.3 不在範圍（明確不做）

- ❌ 8444 16 個缺 inline dark 的頁面補齊（dashboard/events/abnormal/...）
- ❌ 11 主題的 dark 版本補齊（nordic/brutal/earthy/...）
- ❌ 8555 clips.html template 重構（拉出 base.html）
- ❌ 主題切換功能改寫
- ❌ 暗色模式自動切換（依時間/裝置）
- ❌ Sass / PostCSS 編譯流程
- ❌ 配色無障礙調整（contrast ratio 檢查）

---

## 3. 架構

### 3.1 檔案結構改動

```
新增:
  web/static/css/tokens.css              ← 唯一來源

修改:
  web/app.py                              ← 無（既有 context_processor 注入 {{ dark }} 夠用）
  web/templates/base.html                 ← 加 <link> 引入 tokens.css + <html data-theme="...">
  web/templates/wall.html                 ← inline CSS 改用 var()
  web/templates/fleet.html                ← inline CSS 改用 var()
  web/templates/clips.html                ← inline CSS 改用 var()（8444 內的 clips.html）
  web/static/themes/fintech-dark.css      ← 改寫引用 var()

  web/clips_app.py                        ← 無改動
  web/clips_templates/clips.html          ← 加 <link> + inline CSS 改用 var()
  web/clips_templates/nvrs_list.html      ← 加 <link> + inline CSS 改用 var()
  web/clips_templates/nvr_form.html       ← 加 <link> + inline CSS 改用 var()
  web/clips_templates/nvr_import.html     ← 加 <link> + inline CSS 改用 var()

測試:
  tests/test_design_tokens.py             ← 新增：Tokens CSS 內容驗證
  tests/test_8444_dark_uses_tokens.py     ← 新增：8444 19 templates 內無 hardcode 顏色
  tests/test_8555_dark_uses_tokens.py     ← 新增：8555 4 templates 內無 hardcode 顏色
  既有 dark mode 測試維持                   ← 確認 {% if dark %} 行為不變
```

### 3.2 兩 Flask App 共享 Static

**共享可行性**：8555 雖然用獨立 `template_folder`，但 `static_folder` 仍預設在 `web/static/`，因此兩邊都可讀同一檔 `web/static/css/tokens.css`。

**獨立打包考量**：未來 8555 用 pyinstaller 打包時，`web/static/` 仍會被納入（PyInstaller 預設收 `--add-data` 設定的資源）。我們不需要改 Flask static config。

**驗證方式**：
- 8444測試：`url_for('static', filename='css/tokens.css')` 回 `/static/css/tokens.css`
- 8555測試：同樣 URL（兩 Flask app 預設 static URL 路徑一致）

### 3.3 Token 結構（CSS 變數）

```css
/* tokens.css */

/* === 預設為淺色主題 === */
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

    /* 色票 - 狀態 */
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

    /* 間距 */
    --spacing-1: 0.25rem;  /* 4px */
    --spacing-2: 0.5rem;   /* 8px */
    --spacing-3: 0.75rem;  /* 12px */
    --spacing-4: 1rem;     /* 16px */
    --spacing-5: 1.5rem;   /* 24px */
    --spacing-6: 2rem;     /* 32px */

    /* 圓角 */
    --radius-sm: 4px;
    --radius-md: 8px;
    --radius-lg: 12px;
}

/* === Dark 模式覆寫 === */
[data-theme="dark"] {
    --bg-primary: #0c1220;
    --bg-card: #1a1a2e;
    --bg-input: #1a1a2e;
    --bg-table-striped: #16213e;
    --bg-modal: #1a1a2e;
    --bg-secondary: #1e293b;

    --text-primary: #f1f5f9;
    --text-muted: #cbd5e1;
    --text-link: #93c5fd;
    --text-link-hover: #bfdbfe;
    --text-code: #f0abfc;

    --border-primary: #334155;
    --border-secondary: #475569;

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
    --bg-secondary: #1e293b;
}
```

**注意**：為了讓 Flask session `dark=true` 真的能切到 dark theme，HTML 必須有 `data-theme="dark"` 屬性。我們需要：
- 8444 base.html：`<html data-theme="{% if dark %}dark{% else %}light{% endif %}">`
- 8555 4 個 template：同上

這是**新增**邏輯（原本 base.html 沒有 `data-theme` 屬性）。現有 dark mode 切換透過 `{% if dark %}` 條件引入 `fintech-dark.css` 達成。

### 3.4 主題切換策略

**簡化說明**：為了避免現有 11 主題系統被破壞，本 spec **不更動** 11 主題的運作方式。

新設計：
- `tokens.css` 提供「中性設計規格」（色票/字體/間距/圓角）
- 11 主題檔（fintech/nordic/...）各自覆寫 token 變數（如 fintech 把 `--bg-primary` 改成自己的樣式）
- dark mode 切換由 `data-theme="dark"` 觸發，token 變數切換到深色值

**現狀處理**：
- 8444 fintech 主題：依賴 `style.css` 設定 base，token 提供更一致的覆寫基礎
- 8444 其他 10 主題：暫時不動（繼續用主題 CSS）
- 8444 dark mode：依賴 `fintech-dark.css` 覆寫，改寫為引用 `var(--xxx)`

**最小變更**：本 spec 階段，11 主題系統先不消費 `var()`，僅 dark 模式（fintech-dark.css + 3 個有 inline dark 的頁面）改用 token。

### 3.5 範圍控制

- **不做的範圍列在 §2.3** 跟 §3.4，避免 scope creep
- 8555 clips.html 重構（拉出 base.html）不在範圍
- 11 主題全部 dark 化不在範圍
- 8444 16 個頁面補 inline dark 不在範圍

---

## 4. 實作要點

### 4.1 8444 改動

**base.html**（行 1-2）：
```html
<!DOCTYPE html>
<html lang="zh-TW" data-theme="{% if dark %}dark{% else %}light{% endif %}">
<head>
    <meta charset="UTF-8">
    <title>{% block title %}NVR Scanner{% endblock %}</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
    {% if theme == 'fintech' %}
        <link rel="stylesheet" href="{{ url_for('static', filename='themes/fintech.css') }}">
    {% elif theme == 'nordic' %}
        ...
    {% endif %}
    {% if dark %}
        <link rel="stylesheet" href="{{ url_for('static', filename='themes/fintech-dark.css') }}">
    {% endif %}
    ...
```

**注意**：原本 `{% if dark %}` 是引入 `fintech-dark.css`；現在 `data-theme="dark"` 已經切換 token 變數。但 `fintech-dark.css` 仍有額外的 Bootstrap 細節覆寫（alert、badge 等），所以暫時保留引入。

**wall.html / fleet.html / clips.html**（8444 內的 clips.html）：
- 移除 inline `<style>` 區塊內 hardcode 色票
- 改用 `var(--bg-primary)`、`var(--text-primary)` 等
- 保留結構性 CSS（transform、transition、grid layout）

**fintech-dark.css**：
- 內部所有 hardcode 顏色改寫為 `var(--xxx)` 引用
- 保留既有的 Bootstrap 細節覆寫（alert、badge 等）

### 4.2 8555 改動

**4 個 clips_templates/*.html**：
1. 加 `<link rel="stylesheet" href="/static/css/tokens.css">`（或 url_for）
2. 加 `<html data-theme="{% if dark %}dark{% else %}light{% endif %}">`
3. 各自 inline dark CSS 改用 `var(--xxx)`
4. 各自 inline light CSS 跟 dark CSS 高度重疊：抽出共用 class、用 `var(--xxx)` 兩邊都引用

**Architecture 變化**：
- 8555 dark CSS 從「完全 inline hardcode」變「inline + token 引用」
- 還是有 inline `<style>`（不抽 static 檔 — 8555 保持獨立）

### 4.3 測試策略

**新增 `tests/test_design_tokens.py`**：
- `test_tokens_css_exists`：檔案存在
- `test_tokens_css_has_root_block`：淺色 token 定義
- `test_tokens_css_has_dark_block`：深色 token 定義
- `test_tokens_colors_complete`：所有必填色票都有
- `test_tokens_fonts_complete`：字體變數齊全
- `test_tokens_spacing_complete`：間距階梯齊全
- `test_tokens_radius_complete`：圓角階梯齊全

**新增 `tests/test_8444_dark_uses_tokens.py`**：
- `test_8444_wall_html_no_hardcode_colors` 等 3 個測試
- 從 template 讀 raw HTML，用 regex 確認 `#RRGGBB` 數量大幅下降（容忍少數故意保留）

**新增 `tests/test_8555_dark_uses_tokens.py`**：
- 4 個 template 各自的 similar 測試

**既有測試維持**：
- 6 個 8555 dark mode 測試（test_clips_nvr_routes.py 行 371-434）應全綠
- 8444 19 個模板的渲染測試應全綠
- 預期最終：744 → 760 測試

**手動視覺驗證**：
- 啟動 8444，切 dark → 確認 wall/fleet/clips 視覺不變
- 啟動 8555，切 dark → 確認 4 個頁面視覺不變
- 啟動 8444 + 8555 同時切 dark → 確認兩邊色票一致

---

## 5. 風險與緩解

| 風險 | 影響 | 緩解 |
|---|---|---|
| `data-theme` 屬性忘記加到 HTML | dark 模式失效（token 不切換） | 測試檢查 19+4 個 template 都有 `data-theme` 屬性 |
| 11 主題系統衝突 | 某主題原本樣式被 token 覆寫 | 暫不讓主題消費 `var()`，僅 dark mode 用 token |
| 8555 獨立打包失敗 | pyinstaller 沒把 static 檔納入 | 既有 pyinstaller 設定需測試；現在不打包，後續驗證 |
| CSS 變數瀏覽器相容 | IE11 不支援（但 user 不在意） | 現代瀏覽器（Chrome/Firefox/Edge/Safari）支援，使用者為內網 |
| 大量 hardcode 顏色散落 | 一次掃完風險高 | 從既有 3 個 inline dark 頁面（wall/fleet/clips）開始，TDD 驗證視覺後再擴展 |
| 8444 11 主題 dark 缺失 | 切到非 fintech 主題再切 dark 仍掉 fintech-dark 覆寫 | 不處理（明確 scope 排除） |

---

## 6. 驗證

```bash
# 1. 靜態檢查
python -m py_compile web/static/css/tokens.css   # 雖然 .css 不是 .py，但驗證檔案可讀

# 2. 跑既有測試（確保沒破壞）
pytest -q

# 3. 跑新測試
pytest tests/test_design_tokens.py -q
pytest tests/test_8444_dark_uses_tokens.py -q
pytest tests/test_8555_dark_uses_tokens.py -q

# 4. 啟動 8444 + 8555，同時切 dark，視覺驗證
powershell -Command "Get-NetTCPConnection -LocalPort 8444,8555 | Select-Object OwningProcess | Stop-Process -Force"
PYTHONPATH=. python -m web.app 8444 &
PYTHONPATH=. NVR_CLIPS_CLIENT=mock python -m web.clips_app 8555 &
# 用瀏覽器或 Playwright 截圖比對
```

---

## 7. 連結

- 8555 plan commit：258e3f1 (2026-07-30) — 8555 dark mode 起源
- fintech-dark.css：`web/static/themes/fintech-dark.css`（8444 唯一 dark CSS 檔）
- 探索結果 1：8444 dark mode 觸發機制（base.html 行 11-13, 行 46-51）
- 探索結果 2：8555 dark mode 觸發機制（clips_app.py 行 270-280）
- 11 主題列表：base.html 行 14-26
- 既有 dark mode 測試：test_clips_nvr_routes.py 行 371-434
- Memory anchor：session_2026_08_03_untracked_housekeeping_complete.md
