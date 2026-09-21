# Roadmap Issue Tracker — cctv-nvr-dashboard

> **Source of truth**：本檔為團隊進度單一真相。每週五由 maintainer 更新。
>
> **對應 roadmap**：`C:/cc/NVR/_local/cctv-nvr-dashboard-evolution-roadmap-2026-09-17.md`
>
> **進度記號**：✅ 完成 / 🟡 進行中 / ⏸ 暫緩 / ⏳ 待辦

---

## 整體時程（8 週 + Day-0）

| 週次 | 主題 | 狀態 | 說明 |
|---|---|---|---|
| **Day 0** | 資安 Day-0（5 個 hotfix + 5 項 pytest） | ✅ | 2026-09-18 完成（commit `3b29b84`） |
| **Week 1** | Git 分支策略 + CI 補強 | ✅ | 2026-09-18 完成（`aff0f00` + `7571bad` + #003 user 手動設好） |
| **Week 2** | 量化瓶頸（py-spy） | ⏸ | **暫緩**：NVR 192.168.133.141 失聯，待復活後再啟動 |
| **Week 3** | 資料庫分區 Phase 1 | 🟡 | 本週進行中（`feature/db-partition-phase-1` 分支，Plan 撰寫中） |
| **Week 4** | 資料庫分區 Phase 2（歸檔） | ⏳ | 待 Week 3 完成 |
| **Week 5** | 資安提案 B（HTTPS + Flask-Login + rate limit） | ⏳ | 待 Week 3-4 完成 |
| **Week 6** | Blueprints 拆分 | ⏳ | 待 Week 5 完成 |
| **Week 7** | Type Hint + OpenAPI（含 23 個 mypy error） | ⏳ | 待 Week 6 完成 |
| **Week 8** | 最終驗收 | ⏳ | 待 Week 7 完成 |

---

## Week 0 — Day-0 資安修補 ✅

| # | 標題 | 狀態 | Commit / 驗證 |
|---|---|---|---|
| **#001** | Day-0 critical security fixes（SECRET_KEY 強制 env / HOST=127.0.0.1 / CSV-JSON 明碼佔位符 / 9 項 auth pytest） | ✅ | `3b29b84` — 980 項 pytest 全綠 |

**測試覆蓋**：`tests/test_security_basics.py`（9 項） + `tests/test_host_bind.py`（3 項） ✅

---

## Week 1 — Git 分支策略 + CI 補強 ✅

| # | 標題 | 狀態 | Commit / 驗證 |
|---|---|---|---|
| **#002** | 建立 `.github/CODEOWNERS`（@yuang093 鎖關鍵路徑） | ✅ | `aff0f00` |
| **#003** | GitHub Branch Protection Rules | ✅ | **user 手動完成**（2026-09-18 GitHub 網頁端設定） |
| **#004** | `.pre-commit-config.yaml`（detect-secrets + ruff + ruff-format） | ✅ | `7571bad` |
| **#005** | `.secrets.baseline`（203 檔 / 401 baseline secrets） | ✅ | `7571bad` |
| — | ruff-format 全檔套用（131 個檔） | ✅ | `a710239`（user 同意 commit 順便 format） |

**後續待 user 手動**：
- ⏳ `pre-commit install`（user 自行跑；hook 才會自動觸發）

---

## Week 2 — 量化瓶頸（py-spy） ⏸

| # | 標題 | 狀態 | 阻擋原因 |
|---|---|---|---|
| **#007** | py-spy 量化 NVR REST vs DB query 比例 | ⏸ | **NVR 192.168.133.141 失聯**，無法跑真實流量量測 |

**決策樹**（roadmap §1.1 辯論結論）：
- NVR REST > 70% → 先平行化 NVR 掃描（ThreadPoolExecutor）
- DB query > 30% → 考慮 Connection Pool
- 其他 → 維持現狀

**復活條件**：NVR 192.168.133.141 ping 通 + login 成功（任何一台 NVR 復活即可開始 mock 量測）

---

## Week 3 — 資料庫分區 Phase 1 🟡 進行中

> **背景**：88 NVR × 每 5 分鐘 × 永久保留 → 5 年後 2.3 億筆 events。SQLite 不原生支援 PARTITION BY，採「月分區 + view + 應用層 router」模擬。
>
> **決策**：分區粒度 = **月**（`events_YYYY_MM`，roadmap §2.2 層次 1，user 2026-09-18 拍板）

| # | 標題 | 狀態 | 備註 |
|---|---|---|---|
| **#008** | 分區表骨架（`db/migrations/004_create_events_partition.py` + events view + INSTEAD OF triggers） | 🟡 | Plan 撰寫中 |
| **#009** | 應用層 router（`db/event_partition.py`：根據 `occurred_at` 路由 INSERT 到正確月份表） | 🟡 | Plan 撰寫中 |
| **#010** | 既有 132 項測試不破壞驗證（select-only query 走 view 不變；insert/update 走 router） | 🟡 | Plan 已包含驗證策略 |

**核心策略**：
- SELECT 查詢：`FROM events` 透明走 view → **132 項測試零修改**
- INSERT/UPDATE：透過 SQLite `INSTEAD OF` triggers 路由到 `events_YYYY_MM` → SqliteWriter 程式碼微調
- View 定義：`CREATE VIEW events AS SELECT * FROM events_YYYY_MM UNION ALL ...`
- Idempotent migration：偵測 `events` 是否已是 view、是則跳過

**風險管控**：
- ❌ 不直接砍掉現有 `events` 表（用 view 過渡）
- ❌ 不一次寫完所有月份表（只建當月 + 排程建下月）
- ✅ dev 環境跑完整 batch_scan 一次再推 master
- ✅ production 推完監控 1 週

---

## Week 4 — 資料庫分區 Phase 2（歸檔） ✅

| # | 標題 | 狀態 | 備註 |
|---|---|---|---|
| **#011** | events view 動態 UNION 4 張熱表 + 整表 gzip 歸檔（hot_window=4、keep_months=1、每週日凌晨 03:00、產出 `./archives/`） | ✅ | Week 4 完成；6 commits（c8c1149 → 1c2c089）；994 項 pytest 全綠 |

**架構**：view = 4 張熱表 UNION（90 天滑動窗） + 冷表 gzip 封存 + DROP + VACUUM + view rebuild
**驗證**：migration 005（view union）+ `db.archive_partitions` + `scripts/archive_old_partitions.py` CLI + run_worker 每週日凌晨守門
**詳見**：`docs/superpowers/plans/2026-09-21-w4-archive-phase-2.md` + `database_schema.md` §4.1/§4.2

---

## Week 5 — 資安提案 B（HTTPS + Flask-Login + rate limit） ⏳

| # | 標題 | 狀態 | 備註 |
|---|---|---|---|
| **#012** | Flask-Login 整合（users 表 + login_required decorator + session 管理） | ⏳ | 待 Week 4 完成 |
| **#013** | HTTPS 設定（自簽憑證 / cert renewal / reverse proxy for 8444 + 8555） | ⏳ | 部署摩擦最大 |
| **#014** | Rate limit middleware（flask-limiter，保護 `/login` / `/query` / `/devices/*`） | ⏳ | |
| **#015** | Audit log（新增 `audit_log` 表 + 30 項 auth pytest） | ⏳ | 需 Week 3 partition 鋪好寫入層 |
| **#016** | NVR 端帳號降權（強制 `api_reader` 最低權限） | ⏳ | 需 NVR 端配合設定 |

**部署摩擦提醒**：NSSM / systemd 需加 reverse proxy 或 cert 路徑；內部 ops 對「無登入即可用」有 UX 預期，需溝通

---

## Week 6 — Blueprints 拆分 ⏳

| # | 標題 | 狀態 | 備註 |
|---|---|---|---|
| **#017** | `app.py` 拆 Blueprints（auth / dashboard / nvrs / clips / query） | ⏳ | 待 Week 5 完成 |
| **#018** | `clips_app.py` 拆 Blueprints（clips / sync / fetch） | ⏳ | |
| **#019** | 雙 App port 隔離策略文件化（8444 dashboard + 8555 clips） | ⏳ | 維持隔離不合併 |

**雙 App 不合併的紅線**（roadmap §1.3 辯論結論）：合併會引爆 single point of failure

---

## Week 7 — Type Hint + OpenAPI ⏳

| # | 標題 | 狀態 | 備註 |
|---|---|---|---|
| **#020** | 23 個既有 mypy error 修正 | ⏳ | 待 Week 6 完成 |
| **#021** | mypy strict 全檔啟用 | ⏳ | |
| **#022** | OpenAPI 自動生成（apispec 或 flasgger） | ⏳ | 取代 `api_endpoints.md` |

---

## Week 8 — 最終驗收 ⏳

| # | 標題 | 狀態 | 備註 |
|---|---|---|---|
| **#023** | 132 + 50 項 pytest 全綠 | ⏳ | |
| **#024** | CI 5 分鐘內跑完 | ⏳ | |
| **#025** | 文件同步（api_endpoints.md / database_schema.md / overview.md 與程式碼一致） | ⏳ | |
| **#026** | 部署指南更新（DEPLOY.md） | ⏳ | |
| **#027** | Week 8 驗收報告 + 下季 roadmap 規劃 | ⏳ | |

---

## 統計

| 狀態 | 數量 | 百分比 |
|---|---|---|
| ✅ 完成 | 5 / 27 | 18.5% |
| 🟡 進行中 | 1 / 27 | 3.7% |
| ⏸ 暫緩 | 1 / 27 | 3.7% |
| ⏳ 待辦 | 20 / 27 | 74.1% |

---

## 變更紀錄

| 日期 | 變更 | 作者 |
|---|---|---|
| 2026-09-18 | 初版（重建，補上 Week 1 完成狀態 + Week 3 進行中） | Claude Code × 昱安 |
