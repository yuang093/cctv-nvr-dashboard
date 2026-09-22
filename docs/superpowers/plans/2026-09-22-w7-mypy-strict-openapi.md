# Week 7 Implementation Plan: Mypy Strict + OpenAPI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把整個 dashboard 拉到 mypy --strict 零錯誤，並透過 OpenAPI 工具自動產生 8444/8555 雙 App 的 Swagger UI 與 openapi.json，取代手寫的 `api_endpoints.md` §2.1 路由表。

**Architecture:**
1. **Mypy 修復三階段漸進**：從寬鬆設定起步（先有 0 errors 才能 strict）→ 補型別標註 → 啟用 --strict 旗標
2. **OpenAPI 工具**：採 `flasgger`（與 Flask Blueprint 相容、零 schema 強制改寫、支援外部 YAML 檔分離文件）
3. **測試防護**：所有 Task 結尾跑 `pytest -q` 確認 baseline 1005 項全綠；mypy 自身用獨立 CI job 不混進 pytest gate

**Tech Stack:**
- mypy 2.x（最新穩定，現已安裝 mypy 2.3.1）
- flasgger 0.9.x（OpenAPI/Swagger UI 自動產生）
- pyyaml（給 flasgger 解析外部 YAML）
- pre-commit 既有 hook 鏈（Week 1 #004，ruff + ruff-format + detect-secrets）

---

## 1. 起點盤點（2026-09-22 實測）

### 1.1 程式碼現況

| 項目 | 數值 |
|---|---|
| master HEAD | `516a128`（Week 6 #018 收尾） |
| 既有 pytest baseline | **1005** 項（Week 6 11 項 + master 994 項） |
| Flask Blueprints | 8444: 5 bp / 8555: 3 bp（Week 6 完成） |
| 總路由數 | 8444 = 71 條 / 8555 = 33 條 |
| `nvr_scanner.py` 行數 | ~800 |
| `web/app.py` 行數 | 1121 |
| `web/clips_app.py` 行數 | ~250 |

### 1.2 mypy 實際錯誤（**修正 roadmap 的 23 個估計**）

跑 `python -m mypy --ignore-missing-imports nvr_scanner.py discover_event_subtopics.py batch_scan.py db/ web/ tests/` 結果：

- **Found 51 errors in 14 files (checked 151 source files)**
- 錯誤分類（高→低）：
  - `attr-defined` 11 個（缺型別標註導致 object/None 撞 attribute）
  - `arg-type` 9 個（呼叫處型別不符）
  - `assignment` 8 個（assign None/不同型別到變數）
  - `return-value` 5 個（None 撞 int 回傳）
  - `operator` 5 個（None/int 不可相加）
  - `index` 4 個（用 str 索引 str — nvr_scanner.py 的硬編碼 key）
  - `name-defined` 3 個（`app` not defined — `if __name__ == "__main__"` 區塊與 lazy proxy 互撞）
  - `dict-item` 3 個（測試 fixture dict key/value 型別不齊）
  - `misc` / `union-attr` / `no-redef` 各 1 個

### 1.3 工具鏈現況（與 Week 6 結束時相同）

| 工具 | 狀態 | 備註 |
|---|---|---|
| ruff 0.6.9 | ✅ pre-commit 啟用 | Week 1 #004 |
| ruff-format | ✅ pre-commit 啟用 | Week 1 #004 |
| detect-secrets | ✅ pre-commit 啟用 | Week 1 #005 |
| mypy | ❌ **未啟用任何地方** | pre-commit 註解寫錯（CI 也沒跑） |
| pytest | ✅ CI 啟用（3 Python 版本 × 1005 項 ≈ 5 分鐘） | Week 1 + Week 6 |

### 1.4 文件現況

- `api_endpoints.md` §2.1 是**手寫的路由表**（21 行；對應 8444 dashboard 主路由）
- §1.5.4 是手寫的 8555 clips 路由表（8 行）
- 兩份都是「**code 改了文件忘了改**」的脆弱耦合 — Week 7 OpenAPI 自動產生是根治

---

## 2. 三個核心決策點（D1/D2/D3）

### D1. OpenAPI 工具選型（**待 user 拍板**）

| 工具 | OpenAPI 版本 | 與既有 Blueprint 整合 | 學習曲線 | 既有 1005 項 pytest 風險 | 適合場景 |
|---|---|---|---|---|---|
| **A. flasgger** ✅ 推薦 | Swagger 2.0 | 高（既有 Flask bp 直接掛 `@swag_from`） | 低（docstring / YAML 兩種） | **最低**（不動既有 view 程式碼） | Web UI（HTML + 部分 JSON） |
| B. flask-smorest | OpenAPI 3.0 | 中（**需要換掉**既有 Flask bp 為 flask-smorest 的 bp） | 高（Marshmallow schema 強制） | 高（換 bp 等於重寫所有 view decorator） | 純 REST API（JSON in/out） |
| C. 純靜態 YAML | OpenAPI 3.0 | **零整合**（手寫） | 最低（純文件） | 最低（純文件） | 文件導向、Code 與文件解耦 |

**推薦 A（flasgger）理由**：
1. Week 6 剛拆好的 8 個 Flask Blueprint 零修改即可掛 `@swag_from` 裝飾器
2. 既有 1005 項 pytest 完全不會被影響（flasgger 屬 runtime middleware，request flow 不變）
3. 我們的「API」是 dashboard / clips Web UI endpoints（**多為 HTML**，純 JSON 只有少數），flasgger 的 docstring YAML 模式剛好對應
4. flask-smorest 雖然 OpenAPI 3.0 更現代，但要求 bp 換成它的 `Blueprint` class — 會把 Week 6 拆好的 8 bp 全打掉重來
5. 純靜態 YAML 不能解決「code 改文件忘了改」的問題（仍然是手寫）

### D2. Mypy 修復分批策略（**待 user 拍板**）

| 策略 | 順序 | 風險 | CI 引入時機 |
|---|---|---|---|
| **A. 模組分批 + 漸進 strict** ✅ 推薦 | db/ → web/ → tests/ → strict | **最低**（每批獨立 commit） | mypy job 在 strict 啟用當 Task 才啟用；前段只 `local gate` |
| B. 全檔一次修完再 strict | 一次修 51 errors | 高（沒保護網、commit 太大） | mypy strict 一次到位 |
| C. 先 strict 再修 | strict 一啟用 = 100+ 新 errors | **不可行**（會把專案炸掉，沒有分批效果） |

**推薦 A（模組分批）理由**：
1. 對齊 Week 6 Stage A→B 漸進式過渡策略（CLAUDE.md 已建立的紀律）
2. 每個模組（db / web / tests）獨立 commit + 獨立 pytest 驗證
3. db/ 模組是最多錯誤的根源（sqlite_writer.py 就有 3 個 None 處理），先打基底最安全

### D3. CI 防護網時機（**待 user 拍板**）

| 時機 | mypy job 啟用 | 影響 |
|---|---|---|
| **A. strict 啟用前就用 `--non-strict` 當 gate** ✅ 推薦 | Task 1 加 job、每 PR 跑（容忍現有 51 errors 用 `|| true` 暫時不擋）；strict 啟用 Task 才改 fail-on-error | 「mypy 開始追蹤」→ 等修復 Task 逐步降錯誤數 → strict Task 強制零容忍 |
| B. 只在 strict 啟用 Task 加 job | strict Task 之前 PR 完全不跑 mypy | 缺少早期發現、後期炸太大 |

**推薦 A 理由**：
1. 跟 Week 5 #014 一樣的「安全內化優先 + 預設關閉」策略（CLAUDE.md 已建立的紀律）
2. Task 1 起就讓 CI 收集 mypy 數據，未來週報可直接量化進度
3. strict 啟用當 Task 改門檻 — 對齊 Week 6「最後一里」策略

---

## 3. Task 拆解

### Task 1: mypy 基礎建設（最低風險）

**Files:**
- Modify: `requirements-dev.txt`（新增 mypy）
- Create: `pyproject.toml` 的 `[tool.mypy]` section
- Modify: `.github/workflows/ci.yml`（新增 mypy job，先 `|| true`）
- Test: 不需新增（純工具鏈）

- [ ] **Step 1.1**: 在 `requirements-dev.txt` 新增 `mypy>=1.10,<3`
- [ ] **Step 1.2**: 建立 `pyproject.toml` 的 `[tool.mypy]` section（**先用寬鬆設定**，strict 全 False）：
  ```toml
  [tool.mypy]
  python_version = "3.10"
  ignore_missing_imports = true
  warn_unused_ignores = false
  warn_return_any = false
  no_implicit_optional = false
  strict = false
  files = ["nvr_scanner.py", "discover_event_subtopics.py", "batch_scan.py", "db", "web", "tests"]
  ```
- [ ] **Step 1.3**: 在 `.github/workflows/ci.yml` 新增 `mypy` job（**先 `|| true` 不擋 PR**）：
  ```yaml
  mypy:
    name: mypy (warning only)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements-dev.txt
      - run: pip install -r requirements-dev.txt
      - run: python -m mypy || true  # Week 7 Task 1-7: 警告期；Task 8 改 fail-on-error
  ```
- [ ] **Step 1.4**: 跑 `python -m mypy` 確認 51 errors 與預期一致（baseline 量測）
- [ ] **Step 1.5**: 跑 `pytest -q` 確認 1005 仍全綠（純新增 dev dep 不應破壞）
- [ ] **Step 1.6**: Commit
  ```bash
  git add requirements-dev.txt pyproject.toml .github/workflows/ci.yml
  git commit -m "chore(mypy): Week 7 Task 1 — 工具鏈 baseline（CI 警告期）"
  ```

---

### Task 2: 修 db/ 模組錯誤（3 errors，純淨基層）

**Files:**
- Modify: `db/sqlite_writer.py:750,1022,1210`（None 處理）
- Modify: `web/db.py:892,1264,1823`（None 處理）
- Test: 既有測試覆蓋

- [ ] **Step 2.1**: 跑 `python -m mypy db/` 確認錯誤位置（已知 3 個 `int | None` → `int` return-value）
- [ ] **Step 2.2**: 修 `db/sqlite_writer.py:750`：把 `cursor.lastrowid` (Optional[int]) 加型別守衛
  ```python
  row_id = cursor.lastrowid
  if row_id is None:
      raise RuntimeError("INSERT failed: no lastrowid returned")
  return int(row_id)
  ```
- [ ] **Step 2.3**: 修 `db/sqlite_writer.py:1022,1210`：同型別守衛
- [ ] **Step 2.4**: 修 `web/db.py:892,1264,1823`：三處 `int | None` → `int` 改 `assert x is not None` 或型別守衛
- [ ] **Step 2.5**: 跑 `pytest -q tests/test_sqlite_writer.py tests/test_db.py -q`（**只跑這兩個 db 模組測試**，避免 NVR 失聯炸測試）
- [ ] **Step 2.6**: 跑 `python -m mypy db/ web/db.py` 確認從 6 errors → 0
- [ ] **Step 2.7**: Commit
  ```bash
  git add db/sqlite_writer.py web/db.py
  git commit -m "fix(mypy): Week 7 Task 2 — db/sqlite_writer + web/db None 守衛（6 → 0）"
  ```

---

### Task 3: 修 nvr_scanner.py 索引錯誤（4 errors）

**Files:**
- Modify: `nvr_scanner.py:797,804,805,807`（str 索引 str）

- [ ] **Step 3.1**: 跑 `python -m mypy nvr_scanner.py` 確認 4 個 `Invalid index type "str" for "str"`
- [ ] **Step 3.2**: 這是**已知設計**：`camera["logicalId"]` 取 dict 但型別標成 str — 改用 cast 或改型別
  ```python
  cam_id: str = camera["logicalId"]  # type: ignore[arg-type]
  ```
  或更好的方式：把 `camera` 改成 `dict[str, Any]` 型別
- [ ] **Step 3.3**: 跑 `python -m mypy nvr_scanner.py` 確認 4 → 0
- [ ] **Step 3.4**: 跑 `pytest -q tests/`（確認未引入迴歸）
- [ ] **Step 3.5**: Commit

---

### Task 4: 修 batch_scan.py 物件型別錯誤（8 errors）

**Files:**
- Modify: `batch_scan.py:188,193,199,224,231,270,313,320`

- [ ] **Step 4.1**: 跑 `python -m mypy batch_scan.py` 確認 8 個 `"object" has no attribute`
- [ ] **Step 4.2**: 這是 **dynamic config** 撞 mypy — 用 cast 或明確標型別
  ```python
  from typing import cast, Any
  config = cast(dict[str, Any], json.load(f))
  ```
- [ ] **Step 4.3**: 跑 `python -m mypy batch_scan.py` 確認 8 → 0
- [ ] **Step 4.4**: 跑 `pytest -q tests/test_batch_scan.py tests/test_discover_probe.py`
- [ ] **Step 4.5**: Commit

---

### Task 5: 修 web/app.py 錯誤（13 errors，最大模組）

**Files:**
- Modify: `web/app.py:127,180,332,347,624,710,853,938,939,940,1015,1041,1042,1120`

- [ ] **Step 5.1**: 跑 `python -m mypy web/app.py` 確認 13 errors
- [ ] **Step 5.2**: 修 `_parse_nvr_form` no-redef（line 127）：刪除重複 import 或重命名
- [ ] **Step 5.3**: 修 generator `bool` 不符（line 180）：明確標型別
- [ ] **Step 5.4**: 修 None 不可減（line 332）：型別守衛
- [ ] **Step 5.5**: 修 `_MEIPASS` attr-defined（line 347）：用 hasattr 或 `getattr(sys, "_MEIPASS", None)` 包裹
- [ ] **Step 5.6**: 修 `dict[str, str]` not callable（line 624, 710）：檢查是否為函式被當 dict 用
- [ ] **Step 5.7**: 修 AvigilonScanner 3 個 `str | None` → `str` arg-type（line 938-940）：加型別守衛
- [ ] **Step 5.8**: 修 `Name "app" is not defined`（line 1015, 1120）：這是 `if __name__ == "__main__":` 區塊用到 module-level proxy — 改為 `from web.app import app`
- [ ] **Step 5.9**: 修 `str | int` union-attr（line 1041-1042）：用 isinstance 守衛
- [ ] **Step 5.10**: 跑 `python -m mypy web/app.py` 確認 13 → 0
- [ ] **Step 5.11**: 跑 `pytest -q tests/test_web_abnormal.py tests/test_theme_apply.py tests/test_host_bind.py`（app.py 相關測試）
- [ ] **Step 5.12**: Commit

---

### Task 6: 修 web/clips_app.py + web/image_health.py + 既有 blueprint 錯誤（7 errors）

**Files:**
- Modify: `web/clips_app.py:284,462,545`
- Modify: `web/image_health.py:107,109,113`
- Modify: `web/blueprints/nvrs_bp.py:144`
- Modify: `web/clip_retrieval.py:154`

- [ ] **Step 6.1**: 跑 `python -m mypy web/clips_app.py web/blueprints/ web/clip_retrieval.py web/image_health.py` 確認 errors
- [ ] **Step 6.2**: 修 `_SessionStore` 衝突（clips_app.py:284）：Week 6 monkeypatch module-attr 雙 alias 機制需要明確 import
- [ ] **Step 6.3**: 修 None 不可減（clips_app.py:462）：型別守衛
- [ ] **Step 6.4**: 修 `Name "app" is not defined`（clips_app.py:545）：同 Task 5.8
- [ ] **Step 6.5**: 修 PIL `Image` vs `ImageFile` 衝突（image_health.py:107,109）：用 `cast`
- [ ] **Step 6.6**: 修 `BILINEAR` attr-defined（image_health.py:113）：改用 `Image.Resampling.BILINEAR` 或 `Image.BILINEAR`（Pillow 版本差異）
- [ ] **Step 6.7**: 修 `nvrs_bp.py:144` None 處理
- [ ] **Step 6.8**: 修 `clip_retrieval.py:154` tuple 型別
- [ ] **Step 6.9**: 跑 `python -m mypy web/` 確認 51 - 30 → 21 errors（db + scanner + batch + app 都已歸零，剩 web 7 + tests 5）
- [ ] **Step 6.10**: 跑 `pytest -q tests/test_clips_app.py tests/test_clip_retrieval.py tests/test_blueprints_week6.py tests/test_clips_blueprints_week6.py`
- [ ] **Step 6.11**: Commit

---

### Task 7: 修測試與 mock 錯誤（5 errors）

**Files:**
- Modify: `tests/integration/mock_acc.py:481,484,488`
- Modify: `tests/test_sqlite_writer.py:53`
- Modify: `tests/test_web_abnormal.py:73,74`
- Modify: `tests/test_clips_fetch_sync.py:185`

- [ ] **Step 7.1**: 跑 `python -m mypy tests/` 確認 errors
- [ ] **Step 7.2**: 修 `mock_acc.py:481-488` HTTPServer/Thread 初始化 None 處理：用 Optional type + 型別守衛
- [ ] **Step 7.3**: 修 `test_sqlite_writer.py:53` tuple[int, int] → int 簽章錯
- [ ] **Step 7.4**: 修 `test_web_abnormal.py:73-74` dict[str, str] 型別錯
- [ ] **Step 7.5**: 修 `test_clips_fetch_sync.py:185` int → str assign
- [ ] **Step 7.6**: 跑 `python -m mypy` 確認 51 → 0 errors ✅
- [ ] **Step 7.7**: 跑 `pytest -q` 確認 1005 全綠
- [ ] **Step 7.8**: Commit
  ```bash
  git add tests/ web/ db/ nvr_scanner.py batch_scan.py
  git commit -m "fix(mypy): Week 7 Tasks 2-7 — 51 errors → 0（db/scanner/batch/app/clips/tests 模組分批）"
  ```

---

### Task 8: 啟用 mypy --strict（最關鍵轉折）

**Files:**
- Modify: `pyproject.toml` 的 `[tool.mypy]`
- Modify: `.github/workflows/ci.yml`（移除 `|| true`）

- [ ] **Step 8.1**: 把 `pyproject.toml` 的 `[tool.mypy]` 改為 strict 模式：
  ```toml
  [tool.mypy]
  python_version = "3.10"
  ignore_missing_imports = true
  strict = true
  files = ["nvr_scanner.py", "discover_event_subtopics.py", "batch_scan.py", "db", "web", "tests"]
  ```
- [ ] **Step 8.2**: 跑 `python -m mypy` — strict 會暴露**新**的 100+ errors（untyped function bodies 等）
- [ ] **Step 8.3**: 在 `pyproject.toml` 加例外設定（嚴格但不無理）：
  ```toml
  [[tool.mypy.overrides]]
  module = ["tests.integration.*"]
  check_untyped_defs = false  # 整合測試 mock 不強迫型別
  ```
- [ ] **Step 8.4**: 跑 `python -m mypy` 確認剩餘 errors 數（預期 0-20，視 stragglers 而定）
- [ ] **Step 8.5**: 若還有剩餘，逐個修（**可能需要 Task 8.5a/8.5b** 子 Task）
- [ ] **Step 8.6**: 跑 `pytest -q` 確認 1005 仍全綠
- [ ] **Step 8.7**: 移除 `.github/workflows/ci.yml` 的 `|| true`：
  ```yaml
  - run: python -m mypy  # strict mode, fail on error
  ```
- [ ] **Step 8.8**: Commit
  ```bash
  git add pyproject.toml .github/workflows/ci.yml
  git commit -m "feat(mypy): Week 7 Task 8 — 啟用 --strict + CI fail-on-error"
  ```

---

### Task 9: 加 mypy hook 到 pre-commit（最後一里）

**Files:**
- Modify: `.pre-commit-config.yaml`

- [ ] **Step 9.1**: 在 `.pre-commit-config.yaml` 加入 mypy repo：
  ```yaml
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [types-requests, types-PyYAML]
        args: [--config-file=pyproject.toml]
  ```
- [ ] **Step 9.2**: 跑 `pre-commit run mypy --all-files` 確認 hook 正常觸發
- [ ] **Step 9.3**: 跑 `pytest -q` 確認無迴歸
- [ ] **Step 9.4**: Commit
  ```bash
  git add .pre-commit-config.yaml
  git commit -m "chore(pre-commit): Week 7 Task 9 — 加入 mypy hook（取代 9/22 註解）"
  ```

---

### Task 10: OpenAPI 工具整合（flasgger 安裝）

**Files:**
- Modify: `requirements-web.txt`（新增 flasgger）
- Modify: `web/app.py`（工廠內 `Swagger(app)`）
- Modify: `web/clips_app.py`（工廠內 `Swagger(app)`）
- Test: `tests/test_openapi_week7.py`（新增）

- [ ] **Step 10.1**: 在 `requirements-web.txt` 新增 `flasgger>=0.9,<1.0`
- [ ] **Step 10.2**: 跑 `pip install -r requirements-web.txt`
- [ ] **Step 10.3**: 在 `web/app.py` 的 `create_app()` 工廠內加入：
  ```python
  from flasgger import Swagger
  swagger = Swagger(app, template={
      "info": {
          "title": "CCTV NVR Dashboard",
          "version": "1.0.0",
          "description": "8444 dashboard Web UI",
      },
      "basePath": "/",
      "schemes": ["http", "https"],
  })
  ```
- [ ] **Step 10.4**: 在 `web/clips_app.py` 的 `create_clips_app()` 工廠內加入同樣的 Swagger 設定（title="CCTV NVR Clips"，port 8555）
- [ ] **Step 10.5**: 跑 `pytest -q` 確認 1005 仍全綠（Swagger 不應影響既有用例）
- [ ] **Step 10.6**: Commit
  ```bash
  git add requirements-web.txt web/app.py web/clips_app.py
  git commit -m "feat(openapi): Week 7 Task 10 — flasgger 安裝 + 雙 App Swagger template"
  ```

---

### Task 11: 為 dashboard 5 bp 路由加上 YAML doc

**Files:**
- Create: `web/openapi/dashboard/` 目錄 + 30+ YAML 檔
- Modify: `web/blueprints/dashboard_bp.py`、`runs_bp.py`、`nvrs_bp.py`、`scan_bp.py`、`devices_bp.py`（每個 route 加 `@swag_from(...)`）

- [ ] **Step 11.1**: 建立 `web/openapi/dashboard/` 與 `web/openapi/clips/` 兩個目錄
- [ ] **Step 11.2**: 為 dashboard 5 bp 共 35 條 routes 各寫一份 YAML（如 `dashboard_index.yml` / `runs_list.yml`）
- [ ] **Step 11.3**: 每個 route 在 bp 內加 `@swag_from("web/openapi/dashboard/<file>.yml")`
- [ ] **Step 11.4**: 跑 `curl http://127.0.0.1:8444/apidocs/` 確認 Swagger UI 出現
- [ ] **Step 11.5**: 跑 `curl http://127.0.0.1:8444/apispec_1.json` 確認 OpenAPI spec JSON 結構正確
- [ ] **Step 11.6**: 跑 `pytest -q` 確認 1005 仍全綠
- [ ] **Step 11.7**: Commit
  ```bash
  git add web/openapi/dashboard/ web/blueprints/
  git commit -m "feat(openapi): Week 7 Task 11 — dashboard 5 bp 35 條 routes YAML 化"
  ```

---

### Task 12: 為 clips 3 bp 路由加上 YAML doc

**Files:**
- Create: `web/openapi/clips/` 11 個 YAML
- Modify: `web/blueprints_clips/*.py`

- [ ] **Step 12.1**: 為 clips 3 bp 共 10 條 routes 各寫一份 YAML
- [ ] **Step 12.2**: 每個 route 加 `@swag_from(...)`
- [ ] **Step 12.3**: 跑 `curl http://127.0.0.1:8555/apidocs/` 確認 Swagger UI
- [ ] **Step 12.4**: 跑 `pytest -q` 確認 1005 仍全綠
- [ ] **Step 12.5**: Commit
  ```bash
  git add web/openapi/clips/ web/blueprints_clips/
  git commit -m "feat(openapi): Week 7 Task 12 — clips 3 bp 10 條 routes YAML 化"
  ```

---

### Task 13: 測試覆蓋與文件同步

**Files:**
- Create: `tests/test_openapi_week7.py`（5 項新測試）
- Modify: `api_endpoints.md` §2.1 + §1.5.4（標註 OpenAPI 自動產生）
- Create: `docs/openapi-migration.md`（從手寫到自動的遷移紀錄）

- [ ] **Step 13.1**: 新增 `tests/test_openapi_week7.py`：
  - `test_apidspec_json_structure()` — 8444 與 8555 都能 `/apispec_1.json` 拿到
  - `test_swagger_ui_renders()` — `/apidocs/` 200 OK
  - `test_yaml_files_count()` — dashboard 35 + clips 10 + nvr_bp 既有 = 46 條
  - `test_yaml_loadable()` — 所有 YAML 都能被 PyYAML 解析
  - `test_existing_routes_still_200()` — 既有 1005 項 pytest 跑完仍全綠
- [ ] **Step 13.2**: 跑 `pytest -q tests/test_openapi_week7.py` 確認新測試通過
- [ ] **Step 13.3**: 跑 `pytest -q` 確認總測試數 1005 + 5 = 1010 全綠
- [ ] **Step 13.4**: 修改 `api_endpoints.md` §2.1：標註「**本表由 OpenAPI 自動產生**」+ 給 `/apispec_1.json` URL
- [ ] **Step 13.5**: 修改 `api_endpoints.md` §1.5.4：同樣標註
- [ ] **Step 13.6**: 建立 `docs/openapi-migration.md`：記錄從手寫到自動的決策（為什麼選 flasgger、與既有 1005 項測試的相容性、CI 驗證）
- [ ] **Step 13.7**: Commit
  ```bash
  git add tests/test_openapi_week7.py api_endpoints.md docs/openapi-migration.md
  git commit -m "feat(openapi): Week 7 Task 13 — 5 項新測試 + api_endpoints.md 標註自動產生"
  ```

---

## 4. 測試防護總覽（每 Task 結尾都跑）

| Task | pytest 範圍 | 預期數量 |
|---|---|---|
| 1（純工具） | `pytest -q` | 1005 |
| 2（db） | `pytest -q tests/test_sqlite_writer.py tests/test_db.py` | ~50 |
| 3（scanner） | `pytest -q tests/` | 1005 |
| 4（batch） | `pytest -q tests/test_batch_scan.py tests/test_discover_probe.py` | ~30 |
| 5（app.py） | `pytest -q tests/test_web_abnormal.py tests/test_theme_apply.py tests/test_host_bind.py` | ~25 |
| 6（clips + bp） | `pytest -q tests/test_clips_app.py tests/test_clip_retrieval.py tests/test_blueprints_week6.py tests/test_clips_blueprints_week6.py` | ~50 |
| 7（tests） | `pytest -q` | 1005 |
| 8（strict） | `pytest -q` | 1005 |
| 9（pre-commit） | `pytest -q` | 1005 |
| 10（flasgger） | `pytest -q` | 1005 |
| 11（dashboard YAML） | `pytest -q` | 1005 |
| 12（clips YAML） | `pytest -q` | 1005 |
| 13（測試 + 文件） | `pytest -q` | **1010**（+5 新測試） |

**紅燈處理紀律**：任何 Task 結尾 pytest 出現非預期失敗，立即停止並通報 user；不繼續下個 Task。

---

## 5. 文件同步紀律

依 CLAUDE.md「介面 / Schema 變更紀律」：
- 新增 helper / 改 route → 同步更新本檔 + `api_endpoints.md` + `docs/openapi-migration.md`
- 完成每個 Task → 在 `docs/roadmap-issues.md` 勾選對應 #020/#021/#022

---

## 6. 執行模式選擇（待 user 拍板）

| 模式 | 時間 | 風險控制 | 適合情境 |
|---|---|---|---|
| **A. Subagent-Driven** ✅ 推薦 | ~3-4 小時（13 Task × ~15 min） | 每 Task 雙 review（spec compliance + code quality） | 大型重構、需要嚴格防迴歸 |
| B. Inline Execution | ~2-3 小時 | checkpoint batch 驗證 | 中等風險 |

**推薦 A 理由**：
- 13 Task 數量多 + mypy 修復容易引入新迴歸
- 每 Task 雙 review（spec 對齊 + code quality）能在早期抓到 strict mode 漏網
- 與 Week 6 Plan D3 漸進式策略一脈相承

---

## 7. 為什麼這份 Plan 重要

- Week 7 是 8 週計畫的「**品質內化**」章節（型別 + 文件），所有後續 feature 都要 base 在 strict mypy 通過的 codebase 上
- OpenAPI 自動產生是根治「**code 改文件忘了改**」的唯一方法 — 1005 項測試的精準性才能延續
- 51 errors 的 mypy 修復若一次到位，會引爆難以偵錯的連環失敗；模組分批讓每個 commit 都能 revert

**How to apply:**
- 下次 session 從 Task 1 開始 — 先建立 `feature/week7-mypy-openapi` 分支
- 每個 Task 跑完 `pytest -q` 確認 1005 全綠再進下個 Task
- OpenAPI YAML 檔命名統一為 `<bp_name>_<route_name>.yml`
- 任何 strict mode 新錯誤立即停下通報
