# cctv-nvr-dashboard 演進藍圖 — Issue 規劃清單

> **來源**：`cctv-nvr-dashboard-evolution-roadmap-2026-09-17.md` §6 / §7
> **建立日期**：2026-09-18
> **目的**：把 roadmap 拆成可逐步執行的 GitHub Issues
> **範圍**：`cctv-nvr-dashboard` (8444 dashboard)

---

## 已完成

| Issue | 標題 | Commit | 狀態 |
|---|---|---|---|
| #001 | Day-0 資安修補（SECRET_KEY / HOST / Template 明碼密碼 / 9 項 auth pytest） | `e23c8e2` | ✅ committed |

**Deferred**：
- **py-spy 量化 NVR REST vs DB query** → 留到 Week 2，需先解決 NVR 192.168.133.141 失聯

---

## Week 1：Git 分支策略 + CI 補強

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #002 | **建立 `.github/CODEOWNERS`** | `*` → @yuang093；`/db/migrations/`、`/.github/workflows/`、`/database_schema.md` 鎖單一 owner | `ci`, `governance` |
| #003 | **GitHub branch protection rules for `main`** | 2 approvals、require CI passing、CODEOWNERS review、linear history、include admins | `ci`, `security` |
| #004 | **建立 `.pre-commit-config.yaml`** | ruff + ruff-format + mypy + detect-secrets + commitizen；本地 `pre-commit install` 後跑全綠 | `ci`, `dev-experience` |
| #005 | **建立 `.github/workflows/ci.yml`** | 4 jobs：lint / mypy / detect-secrets / pytest (with coverage)；5 分鐘內跑完 | `ci`, `testing` |
| #006 | **修復既有 4 個 regression**（test_clips_app 中文 + coverage 500） | 全部 132+ pytest 全綠 | `bug`, `testing` |

---

## Week 2：效能量化

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #007 | **py-spy profiling：NVR REST vs DB query 比例** | 跑 10 分鐘掃描、產出 profile.svg、決策樹輸出（DB < 30% 用 SQLite singleton；≥ 30% 評估 PG） | `perf`, `P1` |

---

## Week 3-4：資料庫分區 + 歸檔

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #008 | **DB migration 003：`events_2026_09` 分區表 + view** | 既有 `events` table 改名 + 新建當月表 + view UNION ALL；zero downtime；新增 5 項 partition routing pytest | `db`, `schema`, `P1` |
| #009 | **`db/event_partition.py` 應用層 router** | 根據 `event_time` 自動選表；透明給既有 43 個 db 函式用 | `db`, `refactor` |
| #010 | **每月 cron 建立下月分區表** | 1 號自動建立 `events_YYYY_MM` 表；無既有表結構破壞 | `db`, `ops` |
| #011 | **DB migration 004：`events_archive` 表 + 歸檔腳本** | 90 天前資料搬 archive + gzip dump；audit log 寫歸檔筆數；新 5 項歸檔 pytest | `db`, `schema`, `ops` |

---

## Week 5：資安提案 B（HTTPS + Flask-Login + Rate Limit）

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #012 | **Flask-Login 整合 + `/login` route** | session-based auth；30 項 auth mutation test 全綠 | `security`, `P1` |
| #013 | **HTTPS self-signed cert + 反向代理** | 8444 走 HTTPS；NSSM/systemd 設定 cert path | `security`, `deployment` |
| #014 | **`/login` rate limit（10/min per IP）** | flask-limiter；brute force 防護 pytest | `security`, `P1` |
| #015 | **Audit log（DB table）** | NVR CRUD / scan trigger / login 寫 audit_log；既有 routes 加 hook | `security`, `audit` |
| #016 | **NVR 端帳號權限下修（`api_reader` 強制）** | DB schema `nvr_servers.required_role`；UI 提示、import 拒絕高權限 | `security`, `config` |

---

## Week 6：Blueprints 拆分（app.py 1746 行 → < 400 行）

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #017 | **拆分 `web/dashboard_bp.py` Blueprint** | 主 dashboard route 全移入；app.py 只剩 factory + register | `refactor`, `architecture` |
| #018 | **拆分 `web/nvr_bp.py` Blueprint** | NVR CRUD / list / test_connection 全移入 | `refactor`, `architecture` |
| #019 | **拆分 `web/events_bp.py` Blueprint** | events / abnormal / query 全移入 | `refactor`, `architecture` |
| #020 | **拆分 `web/logs_bp.py` Blueprint** | logs / health-reports / pdf 全移入 | `refactor`, `architecture` |
| #021 | **拆分 `web/reports_bp.py` Blueprint** | reports / trends / coverage 全移入 | `refactor`, `architecture` |
| #022 | **schema migration lock 機制** | 多 worker 同時 migration 會 serialize；防止 race condition | `db`, `reliability` |

---

## Week 7：Type Hint + OpenAPI

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #023 | **mypy --strict web/ 全綠** | 0 errors；既有 `Any` / `cast` 改成精準 type | `typing`, `quality` |
| #024 | **OpenAPI 自動生成（從 Flask routes）** | flask-openapi3 / apispec；`/docs` Swagger UI | `docs`, `api` |
| #025 | **取代 `api_endpoints.md` 為 OpenAPI** | 手寫文件刪除；CI 驗證 OpenAPI spec 與實際 route 一致 | `docs`, `ci` |

---

## Week 8：驗收

| Issue | 標題 | Acceptance Criteria | Labels |
|---|---|---|---|
| #026 | **最終驗收：132 + 50 項 pytest 全綠 + CI 5 分鐘內** | pytest --cov 報告 ≥ 80%；CI duration < 5min | `testing`, `milestone` |
| #027 | **production 1 週穩定監控** | 沒 crash；DB < 1GB；scan 沒 deadlock | `ops`, `milestone` |

---

## Milestone 對應

| Milestone | Issues | Due |
|---|---|---|
| Week 0 — Day-0 | #001 | ✅ Done (2026-09-17) |
| Week 1 — Git + CI | #002, #003, #004, #005, #006 | 2026-09-25 |
| Week 2 — Perf | #007 | 2026-10-02 |
| Week 3-4 — DB Partition | #008, #009, #010, #011 | 2026-10-16 |
| Week 5 — 資安 B | #012, #013, #014, #015, #016 | 2026-10-30 |
| Week 6 — Blueprints | #017-#022 | 2026-11-13 |
| Week 7 — Type + OpenAPI | #023, #024, #025 | 2026-11-20 |
| Week 8 — 驗收 | #026, #027 | 2026-11-27 |

---

## 給 gh CLI 的 issue create 指令稿

未來開 issue 時可參考這個格式（user 自行跑，auto mode 擋 gh）：

```bash
gh issue create --milestone "Week 1" --label "ci,governance" \
  --title "建立 .github/CODEOWNERS" \
  --body "$(cat <<'EOF'
## 目標
依 roadmap §3.5 建立 CODEOWNERS，鎖定安全敏感區域。

## Acceptance Criteria
- [ ] `*` → @yuang093（default owner）
- [ ] `/db/migrations/` 鎖單一 owner
- [ ] `/.github/workflows/` 鎖單一 owner
- [ ] `/database_schema.md` 鎖單一 owner

## 驗證
- [ ] GitHub PR 自動要求對應 owner review
EOF
)"
```

---

## Why / How to apply

**Why：** 27 個 issue 一次列出避免「Week 6 才發現 Week 1 的 CODEOWNERS 沒設」之類的依賴錯誤；同時作為 user / Claude Code 共同的 source of truth，未來對進度一目了然。

**How to apply：**
- 每週開始時 review 該 milestone 的 issue，assign owner、設 due date
- issue 完成 → close + 在本檔對應行加 ✅
- 進度落後 → 調整 milestone due date 並在本檔案加註
- 開新 issue 撞到 roadmap 範圍 → 先回此檔案新增一列再開 issue
