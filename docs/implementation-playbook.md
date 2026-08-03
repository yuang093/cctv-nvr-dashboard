# 實作指令手冊：Arisan Gateway 功能吸收到 8444（6 階段 Ultracode 流程）

**對應計畫**：`docs/arisan-integration-plan.md`
**撰寫日期**：2026-07-17
**適用對象**：user 與未來的 Claude session

---

## 0. 工具可用性（誠實版）

### codegraph MCP：**沒裝**

目前 session 沒有 `mcp__codegraph__*` 工具。

### 替代方案（推薦優先順序）

| 工具 | 用途 | 成本 |
|---|---|---|
| **Explore agent** | 大範圍 symbol 搜尋、跨檔案 trace、API 路徑映射 | 中（spawn subagent） |
| **Grep + Glob** | 精準找 symbol / file path / pattern | 低（inline） |
| **Read with offset/limit** | 單檔深入讀 | 低（inline） |
| **LSP**（IDE 內） | go-to-def / references / hover | 即時 |
| **`grep -n`** | 列出全部 uses | 低 |

### 可用 MCP 與 Agent types

| MCP / Agent | 用途 | 狀態 |
|---|---|---|
| `chrome-devtools-mcp` | browser 操作 | ✅ |
| `playwright` | browser E2E | ✅ |
| `context7` | 函式庫 doc | ✅ |
| `microsoft-docs` | MS doc | ✅ |
| `cloudflare` | Cloudflare API | ❌ 未驗 |
| `vercel` | Vercel | ❌ 未驗 |
| **Agent: `Explore`** | 唯讀跨檔搜尋（推薦 code 導航主力） | ✅ |
| **Agent: `Plan`** | 設計 / 架構規劃 | ✅ |
| **Agent: `general-purpose`** | 寫 code / 改檔 / 跑命令 | ✅ |
| **Agent: `feature-dev:code-architect`** | 設計新功能 | ✅ |
| **Agent: `feature-dev:code-explorer`** | 深入 trace | ✅ |
| **Agent: `feature-dev:code-reviewer`** | review 改動 | ✅ |
| **Agent: `pr-review-toolkit:silent-failure-hunter`** | 抓 silent error | ✅ |

---

## 1. Ultracode / Dynamic Workflow 策略

> **核心判斷**：Workflow tool 並非每個任務都需要。
> 小任務（<3 步驟 / 單檔）→ inline 直接做
> 中任務（單一改動 + 跑測試）→ 單 Agent 派工
> 大任務（多檔 + 多步驟 + 需要驗證）→ Workflow tool

| 階段 | Workflow 適用度 | 推薦模式 | 為什麼 |
|---|---|---|---|
| **#1 磁磚點擊跳轉**（0.5d） | ❌ | **inline** | 只改 `dashboard.html` 一檔 |
| **#2 DB schema**（0.3d） | 🟡 | 單 Agent + Explore 預檢 | 跨多檔但單一改動類型 |
| **#3 影像分析核心**（1d） | ✅ | **Workflow pipeline** | 純函式 + 15 條 pytest + 邊界值 |
| **#4 Worker 整合**（1d） | ✅ | **Workflow pipeline** | 跨模組、需真 NVR 端到端驗證 |
| **#5 路由 + UI**（1.5d） | 🟡 | 單 Agent（視進度升 workflow） | 5 routes 獨立但共享 template 風格 |
| **#6 探索網段**（0.5d） | 🟡 | 單 Agent + Explore | 找 Avigilon scanner pattern |

**Pipeline vs Parallel**：
- **Pipeline（預設）**：每階段 agent 接前 agent 結果
- **Parallel（少用）**：只有「所有前 N 階段結果都需要」才用

**Workflow call pattern**（給 Claude 看）：
```
請用 Workflow tool 跑 pipeline 4 stages：
  Phase A: 寫 module 純函式
  Phase B: 寫對應 pytest
  Phase C: 跑 pytest + 修失敗
  Phase D: silent-failure-hunter agent review 實作
```

---

## 2. 6 階段指令範本

每段「**User 指令範本**」區塊都是可以直接複製貼到 Claude 對話的完整指令。

---

### 階段 #1 — 磁磚點擊跳轉（0.5 天）

**User 指令範本**：
```
磁磚點擊跳轉實作（半小時）。
讀 docs/arisan-integration-plan.md 第 5 節。
修 web/templates/dashboard.html 把 4 個磁磚變 <a class="card-tile">，
href 帶 query string（tile 命名見下方表）。
- 在線 → /devices?filter=online
- 訊號中斷 → /wall?filter=signal_lost
- 無訊號 → /wall?filter=no_signal
- 開啟中警示 → /events?status=pending
加 5 條 pytest 測 4 個磁磚的 href + 1 條 dashboard 200 響應。
驗證：pytest -q 全綠 + curl http://127.0.0.1:8444/ 抓 dashboard 內
磁磚 href 對應 query string。
跳過：image_health 磁磚（本階段還沒資料）。
```

**建議**：inline 直接做（小任務，跑單一 agent 不划算）。

**驗證**：
```bash
pytest -q                       # 5 條新測試綠
curl -s http://127.0.0.1:8444/ | grep -E 'tile=' | head -5
```

---

### 階段 #2 — DB Schema 變更（0.3 天）

**User 指令範本**：
```
DB schema 變更（半天）。
讀 docs/arisan-integration-plan.md 第 4 節「資料模型」。
寫 web/db.py 的 init_db() 加：
1. CREATE TABLE IF NOT EXISTS image_health_checks
2. CREATE TABLE IF NOT EXISTS discover_sessions
3. CREATE TABLE IF NOT EXISTS event_kind_catalog（含 17 筆 seed）
4. ALTER TABLE cameras ADD COLUMN last_health_check_id
   （用 try/except 容錯既有資料庫已存在該欄）
同步更新 docs/database_schema.md。
預先派 Explore agent 讀 web/db.py 確認 init_db 既有 pattern。
預先派 Explore agent 確認 events 表 schema 與 seed 17 種主題怎麼塞。
pytest 預期既有全綠（412 → 412）。
手動：sqlite3 nvr_scan.db ".schema" 確認 3 表 + 1 欄位存在。
```

**建議流程**：
1. 派 **Explore** agent 讀 `web/db.py` + `database_schema.md`
2. inline 改 `web/db.py`
3. 派 **general-purpose** agent 跑手動驗證

**驗證**：
```bash
pytest -q                                            # 既有不破壞
sqlite3 nvr_scan.db ".schema" | grep -E "(image_health|discover_sessions|event_kind_catalog)"
sqlite3 nvr_scan.db "SELECT COUNT(*) FROM event_kind_catalog;"  # 預期 17
```

---

### 階段 #3 — 影像分析核心（1 天）

**User 指令範本**：
```
影像分析核心（image_health.py 純函式）。
讀 docs/arisan-integration-plan.md 第 8 節 + CLAUDE.md 的影像分析演算法。
新檔 web/image_health.py：
- @dataclass ImageHealthResult
- analyze_image(jpeg_bytes) -> dict  # blur_var + mean_luma
- is_frozen(jpeg_a, jpeg_b) -> float  # mean abs pixel diff
- 4 種 metric 邊界值測試（15+ 條 pytest）
- 不引 OpenCV，僅 Pillow + stdlib
跑 pytest 確認 +15 條新測試全綠。
請用 Workflow tool 跑 pipeline：
  Phase A: 寫 image_health.py 純函式
  Phase B: 寫 tests/test_image_health.py 邊界值測試
  Phase C: 跑 pytest + 修正失敗
  Phase D: silent-failure-hunter agent review 我的實作
```

**Workflow 結構**：
```javascript
phase('A: implement') → 寫 image_health.py
phase('B: test')      → 寫 15 條 pytest
phase('C: run+fix')   → 跑 pytest，回傳失敗清單
phase('D: review')    → silent-failure-hunter 找漏網之魚
```

**驗證**：
```bash
pytest tests/test_image_health.py -q
pytest -q                                              # 全綠
python -c "from web.image_health import analyze_image; print('OK')"
```

---

### 階段 #4 — Worker 整合（1 天）

**User 指令範本**：
```
Worker 整合（image-health 階段接上 batch_scan）。
讀 docs/arisan-integration-plan.md 第 7 節「Worker 變更」。
跨檔改動：
1. nvr_scanner.py：加 fetch_thumbnail(camera_id) helper
   （30 行 jpeg fetch，複製 clips_app 的 Media API 路徑，
   不要 import web.clip_retrieval 避免跨 app）
2. batch_scan.py：插一段 image_health_check_loop 跑在
   既有 scan() 之後。對 active_cams 抓 2 張 jpeg
   （間隔 5s）→ analyze_image + is_frozen → 寫 image_health_checks
   → 若新觸發則寫 events 表 (source='image_health')
3. 跑一次 python nvr_scanner.py + 手動 .log 看 worker 沒炸
4. tests/test_image_health_integration.py：mock MediaClient，
   回 1 張 jpeg → worker 寫 DB → 觸發 events
Workflow pipeline：
  Phase A: 派 Explore agent 讀既有 batch_scan.py + nvr_scanner.py
  Phase B: 寫 fetch_thumbnail + image_health stage
  Phase C: 跑 pytest 整合測
  Phase D: 真 NVR 一輪手動驗證（要 user 確認）
```

**驗證**：
```bash
pytest tests/test_image_health_integration.py -q
PYTHONPATH=. python nvr_scanner.py 2>&1 | tail -30
sqlite3 nvr_scan.db "SELECT COUNT(*) FROM image_health_checks;"  # 有資料
```

---

### 階段 #5 — 路由 + UI（1.5 天）

**User 指令範本**：
```
5 個新 route + 4 個 template（1.5 天）。
讀 docs/arisan-integration-plan.md 第 5、6 節。

新 routes（web/app.py）：
- GET /wall                    相機牆 grid
- GET /devices                 跨 NVR 設備總覽表（含分頁 filter）
- GET /devices/<id>            單台 cam 詳情 + 健康卡
- GET|POST /devices/discover   探索網段
- GET /health/cameras/<id>     健康歷史

新 templates（web/templates/）：
- wall.html
- device_detail.html
- discover.html
- 加 /devices 列表（用 base.html 既有）

所有新 template 一律用 name_zh 中文顯示（讀 event_kind_catalog）。
記得 Flask template cache：修 template 必須重啟 server + curl 驗新版。
Dark Mode CSS 沿用既有 fintech-dark（不重做）。
不要動 8555。

Workflow：派單一 general-purpose agent，分多次互動（route 寫完先
驗證再寫 template）。

5 條整合測試：
- test_wall_routes
- test_device_detail_route
- test_dashboard_tile_clicks
- test_event_label_i18n
- test_event_kind_catalog
```

**驗證**：
```bash
pytest tests/test_wall_routes.py tests/test_dashboard_tile_clicks.py tests/test_event_label_i18n.py -q
curl -sS http://127.0.0.1:8444/wall -o /dev/null -w "%{http_code}\n"  # 200
curl -sS http://127.0.0.1:8444/devices -o /dev/null -w "%{http_code}\n"
curl -sS http://127.0.0.1:8444/devices/discover -o /dev/null -w "%{http_code}\n"
# 重啟 8444 server 後跑
```

---

### 階段 #6 — 探索網段（0.5 天）

**User 指令範本**：
```
探索網段（半天）。
讀 docs/arisan-integration-plan.md 第 9 節。

POST /devices/discover 流程：
- ipaddress 算 CIDR 內所有 IP
- ThreadPoolExecutor(8 workers) 並行探測
- 探測：requests.get(f"https://{ip}:8443/", timeout=3, verify=False)
  + 看 response header 是否有 "Avigilon" 字串
- 寫 discover_sessions 表
- UI poll 結果頁讓 user 勾選加入 nvr_servers

跳過規則：
- /16 拒絕（防 DoS）
- 既有 NVR IP 跳過
- 超時 3s 內一律 timeout exception 不 raise

派 Explore agent 找現有 scan() 如何對 NVR 做最簡單 probe
（可能直接 GET / 即可）。
2 條整合測試 + 1 條單元測試（CIDR 拒絕）。
```

**驗證**：
```bash
pytest tests/test_discover_routes.py -q
curl -X POST http://127.0.0.1:8444/devices/discover -d "cidr=127.0.0.0/30" -o /dev/null -w "%{http_code}\n"  # 200
sqlite3 nvr_scan.db "SELECT status, COUNT(*) FROM discover_sessions;"
```

---

## 3. 橫切注意事項

| 項 | 規則 |
|---|---|
| Flask template cache | 改完 template **必須**重啟 server，curl 抓新版驗證 |
| DB migration | `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` 用 try/except；**絕不** drop 既有資料 |
| 8555 clips app | **不可碰**；保持獨立部署 |
| 既有契約 | `AvigilonScanner` / `MediaApiClient` 介面不改（只能加 method） |
| Dark Mode | 新 template 沿用既有 `fintech-dark.css`，不重做 |
| Checkpoint 頻率 | 每 1-2 階段寫一個 checkpoint 到 `~/.claude/projects/C--cc/memory/` |
| 17 種事件主題 | UI 一律顯示 `name_zh`，fallback 原文 + (?) |
| 影像異常不立刻 spam | 自製 4 種偵測觸發 events 表時，**要 N 輪確認**才寫（避免誤報） |
| 改完同步 docs | `database_schema.md`、`api_endpoints.md`、`class_interface.md`、`arisan-integration-plan.md` 狀態同步 |
| ulimit token budget | Context 接近上限時先寫 checkpoint 再進下一步 |

---

## 4. 何時該叫 Workflow tool

```
任務複雜嗎？
├─ 不，改單一檔 / 找單一 symbol → **inline**（你自己用 Read/Grep/Glob）
├─ 中：寫 1 模組 + 跨檔引用 → 派**單 Agent**（general-purpose）
└─ 高：多階段多檔 / 需並行驗證 / 需安全 review
    → **Workflow tool**（pipeline 預設，平行只用在「全部需要」時）

進階：Workflow 內每一階段還能再開 Explore subagent
例：phase-5-ui 開 Explore subagent 找既有 template pattern
```

---

## 5. 完整跑完的定義

- [ ] 6 階段全部實作 + 各自 pytest 全綠
- [ ] 17 種事件主題 UI 顯示中文（catalog seed）
- [ ] 既有 14 條功能全保留（跑 8444 舊 route 全部 200）
- [ ] /devices 與 /nvrs 行為不衝突（保留方案 B）
- [ ] 真 NVR 一輪 image_health 跑完有資料進 DB
- [ ] `database_schema.md`、`api_endpoints.md`、`class_interface.md` 同步更新
- [ ] checkpoint 寫到 `~/.claude/projects/C--cc/memory/`
