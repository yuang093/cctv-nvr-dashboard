# Q4 2026（10-12 月）Roadmap — cctv-nvr-dashboard

> **日期**：2026-09-22 規劃
> **更新**：2026-09-23 user 拍板 Q4 優先執行 **方向 C（多站台）**
> **範圍**：Q4 2026（10/1 ~ 12/31）
> **取捨原則**：3 條「可選方向」並陳，由 user 與團隊決定優先順序

---

## ✅ Q4 優先方向（user 2026-09-23 拍板）

### 🥇 方向 C：多站台支援 — Q4 唯一執行項

**決策理由**：
- 既有跨網段網路架構（不同城市 / 不同區域）需要集中管理
- 多區域設備已運轉，未來新增站台頻率上升
- 商業擴充價值最高：把同一套 dashboard 服務多個客戶

**既有基礎**：
- `nvr_servers.site_id` 欄位已預留但未啟用（Week 0 規劃時就埋了 seed）
- 雙 App 隔離（Week 6 #019）已上線，部署摩擦低

**執行建議**（12 週）：
1. **Week 10-11**（2 週）：Schema 補 `sites` 表 + `site_id` 啟用 + Web UI 站台下拉選單
2. **Week 12-13**（2 週）：worker 支援多 ACC endpoint（每站台一組 `host:port`），保留 per-NVR session 隔離（架構二）
3. **Week 14-15**（2 週）：Web UI 站台切換 + per-site 統計 + 跨站台查詢過濾
4. **Week 16-17**（2 週）：部署指南更新（每站台獨立 SQLite 或共用 + site_id 過濾）+ 整合測試

**預期產出**：
- 1 套 dashboard 同時服務多個站台 / 多個客戶
- 站台層級統計（每站台 NVR / camera / 異常數）
- 跨站台查詢過濾（保留單站台既有查詢行為）

**資源**：~35-45 人天、1 個工程師

---

## 次優先（Q5 候選）— 暫緩到 2027 Q1

### 方向 A：型別完整化
- 等方向 C 落地 + DB schema 穩定後再做（避免 schema 變更期間還要同步型別）
- 預估 2027 Q1 啟動（Week 18-21）

### 方向 B：即時性（SSE / WebSocket）
- 排最後（既有 5 分鐘 worker 週期堪用；緊急度最低）
- 預估 2027 Q2 啟動（Week 22-25）

---

## 三條方向（並列、不互斥）

### 方向 A：型別完整化（Week 9-10 候選）

**目標**：把 mypy 從「嚴格 subset」升到「full --strict」。

**現況**：
- `disallow_untyped_defs = false`（Week 7 暫不啟用）
- 估計 1581 個 untyped-def 待補

**執行建議**（12 週）：
1. **Week 9**：先把 `db/sqlite_writer.py`（DB 寫入層）型別補完（~300 def，業務核心）
2. **Week 10**：`web/db.py` + `web/fleet.py` 查詢層（~250 def）
3. **Week 11**：所有 bp（~400 def，分 8 個檔）
4. **Week 12**：`tests/` 型別標註 + 啟用 `disallow_untyped_defs = true`

**產出**：mypy 0 → 0 errors（full strict）、測試不破。

**取捨**：投入大量時間換「IDE 自動補完 + 重構信心」。

---

### 方向 B：即時性（Week 11-12 候選）

**目標**：異常發生 → 5 秒內 Slack/Teams 通知 + Web UI 即時更新。

**現況**：
- Webhook 推播已上線（Slack/Teams 格式），但只在 worker 掃描週期觸發（最長 5 分鐘延遲）
- Web UI 無即時更新（要 reload 頁面）

**執行建議**（8 週）：
1. **Week 11**：NVR Server-Sent Events 訂閱（替代輪詢 ACTIVE events），worker 收到新事件立即觸發 webhook
2. **Week 12**：Web UI 端 SSE endpoint + JS EventSource client，dashboard 異常卡 / events 列表即時刷新

**產出**：異常 0-5 秒通知（vs 現在 0-5 分鐘）。

**取捨**：NVR 端不一定支援 SSE（需實測 ACC 8.7+ 是否開 SSE），若不支援就改 WebSocket。

---

### 方向 C：多站台支援（Week 9-12 候選）

**目標**：支援多個 ACC 站台（不同城市 / 不同客戶），Web UI 切換站台。

**現況**：
- `nvr_servers.site_id` 欄位已預留但未啟用
- worker 假設「一個 ACC cluster」

**執行建議**（12 週）：
1. **Week 9**：Schema 補 `sites` 表 + `site_id` 啟用 + Web UI 站台下拉選單
2. **Week 10**：worker 支援多 ACC endpoint（每站台一組 `host:port`）
3. **Week 11**：Web UI 站台切換 + per-site 統計
4. **Week 12**：部署指南（每站台獨立 SQLite 或共用 + site_id 過濾）

**產出**：一套 dashboard 服務多個客戶。

**取捨**：與既有單站台使用者無關，是「賣給其他客戶」的擴充。

---

## 建議優先順序（給 user 決策）

| 排名 | 方向 | 理由 |
|---|---|---|
| 🥇 | **A 型別完整化** | 投資報酬率最高：後續所有重構都受惠、IDE 自動補完加速開發 |
| 🥈 | **C 多站台** | 商業擴充：把同一套產品賣給多個客戶 |
| 🥉 | **B 即時性** | UX 改善：但既有 5 分鐘延遲已堪用，緊急度最低 |

---

## 資源估算

| 方向 | 人天 | 風險 |
|---|---|---|
| A | 30-40 人天 | 低（純技術債清理） |
| B | 25-30 人天 | 中（NVR SSE 相容性未知） |
| C | 35-45 人天 | 高（商業邏輯變更、部署複雜度升級） |

**Q4 內 1 個工程師** 可做完 1 條方向；2 個工程師可並行做 2 條。

---

## 不在 Q4 roadmap 的事（避免 scope creep）

- ❌ 不做「影像 AI 辨識」（已超出監控儀表板定位）
- ❌ 不做「跨站台聯合報表」（方向 C 只做切換、不做聯合查詢）
- ❌ 不做「行動 App」（Web UI 已 RWD，暫不開原生 App）
- ❌ 不重構既有程式碼（除非該檔案在方向 A/B/C 觸及範圍）

---

## 變更紀律

每季末（12/31）回頭 review：
1. 本季 3 條方向完成率？
2. 哪些方向超出預期？哪些方向被取消？原因？
3. 下季 3 條方向重新洗牌

---

## 變更紀錄

| 日期 | 變更 |
|---|---|
| 2026-09-22 | 初版規劃（3 條可選方向並陳） |
| 2026-09-23 | **user 拍板方向 C 為 Q4 唯一執行項**；A / B 順延至 2027 Q1 / Q2 |

---

**How to apply:**

- 本檔是「目錄」不是「合約」— 任何方向可在 Q4 內任一週啟動
- user 與團隊決策會議前先讀本檔 + `docs/w8-acceptance-report.md` 評估現況
- 不要把 Q4 roadmap 當 sprint backlog 嚴格執行 — 季度內可調整
