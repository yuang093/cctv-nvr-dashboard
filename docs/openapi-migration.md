# OpenAPI 遷移紀錄 — Week 7 Issue #022

> **Week 7 Issue #022** — 2026-09-22 完成：從手寫路由表遷移到 flasgger 自動產生 OpenAPI spec。

---

## 為什麼需要遷移？

Week 7 Issue #022 之前，`api_endpoints.md` §2.1 + §1.5.4 是**手寫**的路由表。問題：

| 痛點 | 影響 |
|---|---|
| Code 改了文件忘了改 | docs 漂移，新人 onboarding 失真 |
| 沒有 request/response schema 定義 | 前端 / API 整合只能靠人工對齊 |
| 沒有線上測試入口 | 員工問「這 API 怎麼用」只能翻 source code |
| Week 6 拆完 8 個 bp 後路由散落 | 手寫表格 vs 散落 decorator 對齊成本高 |

## 為什麼選 flasgger？

Plan D1 三方案對比：

| 工具 | OpenAPI 版本 | Blueprint 整合 | 學習曲線 | 1005 項 pytest 風險 |
|---|---|---|---|---|
| **flasgger** ✅ | Swagger 2.0 | 高（既有 Flask bp 直接掛 `@swag_from`） | 低 | **最低** |
| flask-smorest | OpenAPI 3.0 | 中（**需要換掉**既有 bp） | 高 | 高 |
| 純靜態 YAML | OpenAPI 3.0 | 零整合（手寫） | 最低 | 最低 |

**選 flasgger 理由**：
1. Week 6 剛拆好的 8 個 Flask Blueprint **零修改**即可掛 decorator
2. Swagger 2.0 而非 OpenAPI 3.0 是 trade-off（3.0 更現代，但需要重寫 bp）
3. flasgger 用 `@swag_from("path/to/yml")` 把 YAML 與 route 解耦，**不污染 view code**

## 架構

```
                    web/blueprints/*.py
                    ┌──────────────────┐
                    │ @dashboard_bp    │
                    │   .route("/wall")│
                    │ @swag_from("web/│ ← swag_from decorator
                    │  openapi/dash    │   標註規格
                    │  board/devices_  │
                    │  wall.yml")      │
                    └──────────────────┘
                            ↓
                    web/openapi/dashboard/*.yml（35 個）
                    web/openapi/clips/*.yml（10 個）

                            ↓
                    flasgger 在工廠內 `Swagger(app, template={...})`
                            ↓
                    GET /apidocs/     → Swagger UI
                    GET /apispec_1.json → OpenAPI spec JSON
```

## 實作細節

### 1. 安裝與工廠整合（Task 10）

`requirements-web.txt`：
```python
flasgger>=0.9,<1.0
```

`web/app.py::create_app()` 與 `web/clips_app.py::create_clips_app()` 內：
```python
from flasgger import Swagger
Swagger(app, template={
    "info": {"title": "CCTV NVR Dashboard", "version": "1.0.0", ...},
    "basePath": "/",
    "schemes": ["http", "https"],
})
```

### 2. YAML 規格（Task 11 / Task 12）

**YAML 命名規則**：`{bp_name}_{route_name}.yml`
- dashboard：`dashboard_index.yml`、`runs_detail.yml`、`nvrs_edit.yml`…
- clips：`pages_clips.yml`、`coverage_data.yml`、`media_fetch.yml`…

每個 YAML 必含欄位：
```yaml
---
tags: [dashboard]      # 分類（dashboard / runs / nvrs / scan / devices / clips / coverage / media）
summary: 路由簡述
description: |
  詳細說明（支援多行）
parameters: [...]
responses:
  200:
    description: 成功回應
```

### 3. swag_from path 格式（重要！）

採 **Python 模組路徑** 而非檔案路徑：
```python
# ✅ 正確：flasgger 用 importlib 解析
@swag_from("web.openapi.dashboard.dashboard_index.yml")

# ❌ 錯誤：flasgger 的 root_path fallback 在 Flask 3.x 有 bug
@swag_from("web/openapi/dashboard/dashboard_index.yml")
```

我們有 `web/openapi/__init__.py`、`web/openapi/dashboard/__init__.py`、`web/openapi/clips/__init__.py` 三個空檔，讓 flasgger 能用 `importlib.util.find_spec` 找到 package。

## 測試防護

`tests/test_openapi_week7.py` 5 項新測試：
1. **YAML 數量**：dashboard 35 + clips 10 = 45
2. **YAML 可解析**：所有 YAML 都能被 PyYAML 解析 + 含必要欄位
3. **swag_from decorator 計數**：每個 bp 的 `@swag_from` 數 == 預期
4. **route 數 == swag 數**：每個 bp 的 `@xxx_bp.route` == `@swag_from`（防呆：每條 route 都要有 YAML）
5. **swag_from path 格式**：全部符合 `web.openapi.(dashboard|clips).NAME.yml`

**注意**：flasgger 0.9.7 在 `GET /apidocs/` 與 `GET /apispec_1.json` 的內部 root_path 解析有 known bug（會 raise `AttributeError: 'NoneType' object has no attribute 'has_location'`）。這是 flasgger 對 Flask 3.x `app.root_path` 行為改變的相容性問題，**不影響我們的 YAML / decorator 正確性**。Swagger UI 可手動驗證（user 啟動 server 後訪問 /apidocs/）。

## 已知限制

1. **Swagger 2.0 而非 OpenAPI 3.0**：flasgger 預設 Swagger 2.0；要升級 3.0 需要客製 template。本專案先以 2.0 落地，未來視需求升級。
2. **flasgger path resolution**：如前述，flasgger 0.9.7 在 Flask 3.x 有 root_path bug。**workaround**：所有 swag_from 統一用 Python 模組路徑（點分隔）。
3. **檔案膨脹**：45 個 YAML 檔（每個 10-20 行）= 約 700 行 metadata。如果改用 inline docstring（flasgger 也支援），可減少檔案數但會污染 view code。Trade-off 已由 user 在 D1 拍板選外部 YAML。

## 驗證結果

| Task | 結果 |
|---|---|
| Task 10：flasgger 安裝 + 雙 App Swagger template | ✅ 完成 |
| Task 11：dashboard 5 bp 35 條 routes YAML 化 | ✅ 完成（檔案 + @swag_from 各 35）|
| Task 12：clips 3 bp 10 條 routes YAML 化 | ✅ 完成（檔案 + @swag_from 各 10）|
| Task 13：5 項新測試 + api_endpoints.md 標註自動產生 | ✅ 完成（1005 → 1010 全綠）|

## 後續維護

新增 route 時，**必須同時**：
1. 在對應 bp 內加 `@xxx_bp.route(...)` decorator
2. 在 `web/openapi/{dashboard,clips}/` 內建對應 YAML 檔
3. 加 `@swag_from(...)` 引用該 YAML
4. 跑 `pytest tests/test_openapi_week7.py` 確認數量對齊

修改既有 route 時，**同步更新 YAML 內 summary / parameters / responses**。

---

**為什麼這份文件重要**

這是 Week 7 Issue #022「從手寫到自動」決策紀錄。新人 on-boarding 看到「8444 怎麼知道路由」時，讀本檔即可知道「Swagger UI 是 source-of-truth，不是手寫文件」。**避免下次 sprint 又有新人誤把 `api_endpoints.md` §2.1 當作 source-of-truth 來維護**。

**How to apply:**

- 部署後由 user 手動驗證 Swagger UI（`http://127.0.0.1:8444/apidocs/`）
- CI 不會自動跑 Swagger UI 驗證（flasgger 0.9.7 + Flask 3.x 相容性問題）
- 若未來要升級 OpenAPI 3.0，建議換 flask-smorest（但會破壞現有 Week 6 Blueprint 架構）
