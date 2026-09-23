# Week 8 最終驗收 — 設計規格

> **Issue #023 ~ #027** — 2026-09-22 啟動：盤點 8 週演進成果，產出可驗收的文件 + 部署 + 下季 roadmap。

---

## 背景

8 週 roadmap（Day-0 + Week 1-8）已於 2026-09-22 完成核心實作：
- Week 1 ✅ Git 分支策略 + CI 補強
- Week 3 ✅ DB 月分區 Phase 1
- Week 4 ✅ DB 動態 view + 冷資料歸檔
- Week 5 ✅ 資安 + HTTPS + rate-limit + audit
- Week 6 ✅ Blueprints 拆分（8444 + 8555）
- Week 7 ✅ mypy strict subset + OpenAPI

Week 8 是**驗收週**，不是開發週。5 個 Issue 都是文件 / 驗證 / 規劃性質，目的是把過去 8 週的成果「收斂成可對外 demo、可對內維護」的狀態。

---

## 設計目標

### Issue #023 — 1010 項 pytest 全綠（驗證）
- **不是新增 50 個測試**（roadmap-issues.md 寫「132 + 50」是過時誤算）
- 重新對齊 metric 定義：「1010 pytest 全綠」就是當前可驗收狀態
- 包含：945 unit + 65 integration + 5 OpenAPI（Week 7）+ ...
- 在 CI 內 fail-on-error 跑（不能再用 `|| true`）

### Issue #024 — CI 5 分鐘內跑完（效能驗證）
- 當前：3 個 Python 版本矩陣 × 89 秒 ≈ 4.5 分鐘（單 run 89s 已驗證）
- 目標：合併矩陣到單版本 3.12（其他版本用本地 smoke）+ 加 cache 加速 pip install
- 預估：3.12 單版本 ~90 秒 + cache + 並行 job（pytest + mypy 並行）→ < 3 分鐘

### Issue #025 — 文件同步（code-as-truth 對齊）
- `api_endpoints.md` §2.1 路由表**已標註自動產生**（Week 7 完成）；保留 §2.2 參數表 + §2.3 歷史 PDF + §2.4 `/query` 安全規則
- `overview.md` v1/v2 章節內容與當前架構對齊（8444 + 8555、雙 App、Blueprint）
- `database_schema.md` §4-5 章節對齊 Week 4 view union + Week 5 audit_log
- `README.md` 反映當前功能狀態
- `CHANGELOG.md` 加 Week 5-7 三個 release entry

### Issue #026 — DEPLOY.md 對齊當前部署模型
- Week 5 起 HTTPS / Flask-Login / rate-limit / audit 全上線 → 部署摩擦大升級
- 現有 DEPLOY.md 還是 exe 模式（30 秒極簡版）→ 內網 + 帳號 + HTTPS 該怎麼做
- 補「Week 5+ 部署指南」段：reverse proxy (Caddy) + SECRET_KEY env + 內網白名單 + audit 歸檔
- 「疑難排解」補常見 5 種新症狀（login 失敗 / rate limit 觸發 / audit 寫入失敗 / HTTPS 憑證過期 / Swagger UI 404）

### Issue #027 — 驗收報告 + 下季 Roadmap
- `docs/w8-acceptance-report.md`：8 週總體回顧（產出、metric、commit 統計、教訓）
- `docs/roadmap-q4-2026.md`：下季 3 大方向（型別補強 / 即時性 / 多站台）+ 12-16 週時程

---

## 架構決策

### D1 — CI 矩陣簡化為單 Python 3.12
- **理由**：當前 3 版本矩陣 × 89s ≈ 4.5 分鐘超過 5 分鐘 budget 的 90%
- **trade-off**：放棄 3.10/3.11 兼容性測試（Week 8 後維護單版本）
- **配套**：本機保留 `tox -e py310,py311,py312`（手動驗證用，不走 CI）

### D2 — 文件同步採「code-driven fact-check」策略
- **不是全文重寫** — 是「跑 `pytest --collect-only` 統計 routes、統計表、統計 commits」對齊數字
- **不是自動產生** — 程式碼已是 source-of-truth（Week 7 OpenAPI 自動化後，文件是 reflection）
- **驗收腳本** `scripts/docs_factcheck.py`：掃所有 .md 文件的「表 / 數字 / 函式名」與程式碼比對，列出漂移項

### D3 — DEPLOY.md 重寫為「雙 App 部署指南」
- 既有 exe 30 秒版保留（給內部 demo 用）
- 新增「8444 + 8555 + Worker 三 process 部署」段（Caddy reverse proxy / HTTPS / SECRET_KEY / audit）
- 對應 `docs/dual-app-isolation.md` 已有的隔離策略，加 PR 後實作的部署細節

### D4 — 下季 Roadmap 給 3 條「可選方向」而非死時程
- **方向 A**：型別完整化（`disallow_untyped_defs = true` + 1581 untyped-def 補完）
- **方向 B**：即時性（SSE / WebSocket 即時事件流 + Slack/Teams 內建 alert）
- **方向 C**：多站台支援（site_id 已有欄位，擴 worker 多 ACC 實例 + Web UI 多站台切換）
- 寫成「目錄」而非「必做清單」，讓 user 與團隊下一季再決定優先順序

---

## 不做（YAGNI 紅線）

- ❌ 不實作「即時 SSE / WebSocket」（roadmap-issues.md 已標 ⏳，屬下季）
- ❌ 不實作「多站台」（`site_id` 欄位已預留但未啟用）
- ❌ 不重寫文件全文（只對齊數字與章節，避免 context 漂移）
- ❌ 不動 `pyproject.toml` 的 mypy strict（Week 8+ 才升 `disallow_untyped_defs`，避免雪崩）
- ❌ 不動既有 .py 任何業務邏輯（純文件 + CI 配置 + 驗證腳本）

---

## 驗收定義（Definition of Done）

| Issue | 驗收物 | 驗收方式 |
|---|---|---|
| #023 | 1010 pytest + 0 mypy errors | `pytest -q` 全綠 + `mypy` 0 errors |
| #024 | CI < 5 分鐘（單矩陣版本） | 從 GitHub Actions 抓最新 run duration < 300s |
| #025 | 文件 fact-check 全綠 | `python scripts/docs_factcheck.py` 0 漂移項 |
| #026 | DEPLOY.md 含雙 App + HTTPS + audit 段 | grep 章節 + 對照 `docs/dual-app-isolation.md` |
| #027 | 驗收報告 + Q4 roadmap | 兩個 .md 檔 commit 進 master |

---

## 風險

| 風險 | 機率 | 影響 | 緩解 |
|---|---|---|---|
| 簡化 CI 矩陣破壞其他 Python 版本兼容性 | 中 | 高 | 保留 tox 本機 smoke + 部署前手動測 3.10 |
| docs_factcheck 過嚴，誤報格式漂移 | 中 | 中 | 從寬鬆開始（只檢 routes / 表名 / 函式名），再加嚴 |
| DEPLOY.md 重寫破壞既有 30 秒極簡版 | 低 | 中 | 保留「快速開始」段在最頂，新增「Week 5+ 部署」段在後 |
| 下季 roadmap 寫太死，下季又大改 | 中 | 低 | 用「方向」而非「時程」呈現，給 user 決策空間 |

---

## 不變更項目（給未來 reviewer）

- 業務邏輯：所有 `web/blueprints/`、`db/`、`batch_scan.py`、`nvr_scanner.py` 不動
- 既有 .py 型別標註（Week 7 已 0 errors）：不回頭改
- 既有測試（1010 項）：不刪、不改
- 既有 OpenAPI YAML 規格（45 條）：不動
- 既有部署腳本（`.bat` / `.sh` / `.ps1`）：不動

---

**How to apply:**

- Week 8 是「驗收週」，每個 Task 結束跑一次 `pytest -q` + `mypy` 確認 baseline
- 文件 fact-check 從最簡單的 5 個檢查開始（routes / 表名 / 函式名 / 行數 / commit 數），不要一開始就複雜化
- CI 改完必須從 GitHub Actions 實際觸發一次確認 < 5 分鐘（不要只看本地秒數）
- 下季 roadmap 是「給 user 決策用的目錄」，不是「下季 sprint 要全部做完的承諾」

