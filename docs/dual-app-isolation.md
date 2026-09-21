# 8444 Dashboard + 8555 Clips 雙 App Port 隔離策略

> **Week 6 Issue #019** — 2026-09-21 決議：維持兩 process（port 8444 + 8555）獨立運行，不合併 single Flask app。

---

## 為什麼不合併？

2026-08-04（Spec F）和 2026-08-06（Spec G）期間，曾考慮把 clips 合併進 dashboard。

### 紅線決議（roadmap §1.3）

| 評估面向 | 合併 | 維持隔離 |
|---|---|---|
| **單點故障** | 一個 process 當掉，全公司 dashboard + 機票回放一起掛 | 任一 process 當掉，另一條線仍可用 ✅ |
| **Memory 負載** | fmp4 streaming 期間 baseline memory 200MB+ → dashboard 也跟著抖 | 8444 dashboard 平均 80MB、只跑 dashboard 不受 clips 大檔案影響 ✅ |
| **Crash blast radius** | 8555 clips NVR 5xx streaming bug 可能把整個 dashboard 拖死 | clips 是 raw 多媒體 byte stream（30s clip ≈ 16MB），獨立 process 隔離 ✅ |
| **版本演進** | 兩個 UI 共用模板目錄 → 不能單獨升 Flask / 升 Pillow | clips 端可獨立升級 ✅ |

**結論**（user 2026-09-21 拍板）：**維持兩個 process 隔離**。

---

## 架構

```
                    LAN 內網
                  ┌─────────┐
       employee → │  Caddy  │ → reverse proxy
                  │ reverse │   ├─ /dashboard → http://127.0.0.1:8444
                  │  proxy  │   └─ /clips    → http://127.0.0.1:8555
                  └─────────┘
                              ▲
              同 SQLite 檔案  │
              (READ only  ❌  │ write via worker)
                              │
                  ┌──────────────┐
                  │ nvr_scan.db  │ (Background Worker)
                  └──────────────┘
```

- **8444 dashboard**：v2 Web UI（之前 dashboard.hmlt、runs、nvrs、events、wall、devices、abnormal 等 35 條 URLs）
- **8555 clips**：Phase 2.7 影片調閱（給其他部門、兩個 NVR 跨家時的調閱跨 port）

兩 process 各自：
- 啟動 `/run_web.ps1` / `/run_clips.ps1`
- 獨立 SECRET_KEY（`NVR_WEB_SECRET_KEY` vs `NVR_CLIPS_SECRET_KEY`）— 確保 Session cookie 不互通
- 共用 `./nvr_scan.db` SQLite 檔（但 **READ ONLY**；寫入由 background worker 排程負責）
- 共用 `AVIGILON_USER_NONCE` / `AVIGILON_USER_KEY`（同一組整合帳號）

---

## Cross-Port 通訊機制

### 8555 → 8444：deep-link 從 coverage.html 跳 /trends

Spec G Batch C Task 11：8555 的 coverage.html JS 不能用相對路徑 `/trends`（會打到 8555/trends — 該路徑不存在）。

**解法**：context_processor 注入 `NVR_DASHBOARD_URL` 全域變數：

```python
# web/clips_app.py / web/blueprints_clips/pages_bp.py
import os
base = os.environ.get("NVR_DASHBOARD_URL", "http://127.0.0.1:8444").rstrip("/")
return dict(dashboard_url=base)
```

```javascript
// web/clips_templates/coverage.html
window.NVR_DASHBOARD_URL = {{ dashboard_url|tojson }};
// 之後 JS 動態組合 `http://192.168.1.100:8444/trends?cam_id=...&range=24h`
fetch(`${window.NVR_DASHBOARD_URL}/api/cams/${encodeURIComponent(camId)}/health`)
```

### 8444 → 8555：dashboard.html 內 NVR clip 預覽（未來 spec）

預留 env `NVR_CLIPS_URL`（與 `NVR_DASHBOARD_URL` 對稱），目前未有對應功能。

---

## Session / SECRET_KEY 分離

| App | Env var | 用途 |
|---|---|---|
| 8444 | `NVR_WEB_SECRET_KEY` | flash() + session dark mode toggle + WSGI 二級緩衝 |
| 8555 | `NVR_CLIPS_SECRET_KEY` | dark mode + session cache（in-memory SessionStore 不依賴 session） |

**Wk 5 #013 加固**：
- 8444 強制 require env var、缺則 raise
- 8555 有 fallback dev key（讓 test fixture 不需 monkeypatch env）；production 須設 `NVR_CLIPS_SECRET_KEY`

---

## 部署拓樸（LAN → Internet）

內網：

```bash
# 8444（預設 bind 127.0.0.1；LAN 需改 0.0.0.0）
NVR_WEB_HOST=0.0.0.0 ./run_web.sh

# 8555（已預設 0.0.0.0；給其他部門 LAN）
./run_clips.sh
```

對外網（reverse proxy 模式，Caddy 推薦）：

```caddy
dashboard.example.com {
    reverse_proxy 127.0.0.1:8444
}
clips.example.com {
    reverse_proxy 127.0.0.1:8555
}
```

兩個 HTTPS 證書獨立管理（Caddy 自動 renew）：

- dashboard.example.com → Let's Encrypt cert
- clips.example.com → Let's Encrypt cert

Wk 5 #013 部署詳見 `docs/deployment_backup.md` § reverse proxy。

---

## 為什麼不用同一 Flask app 多 url_prefix？

如果合併成 single Flask app、`url_prefix='/clips'` 仍能達到 URL 區隔，但:

| 屬性 | 維持兩 process | single Flask app |
|---|---|---|
| Crash isolation | ✅ | ❌ |
| Memory isolation（fmp4 streaming 不污染 dashboard） | ✅ | ❌ |
| 兩條獨立 hot reload 流程 | ✅ | ❌（PyInstaller / debug 互相影響） |
| 兩條獨立 deploy | ✅ | ❌（須 restart 全部） |

結論：**兩 process 隔離是原則，不是過渡**。

---

## 從合併回到隔離的可能性？

永遠可以（flask blueprint 化後結構清晰）。反之如果要從隔離合併回 single Flask app，需要：

1. 在 create_app 內同時 `register_blueprint(scan_bp, url_prefix='/dashboard')` 與
   `register_blueprint(media_bp, url_prefix='/clips')` 兩組
2. 合併 `dashboard_url` / `clips_url` 成單 env var
3. 移除兩條獨立 process / reverse proxy 設定
4. 重新跑 load test 驗證 single process 能撐住 fmp4 stream 拖的 memory pressure

短期規劃（Week 7+）不做合併；即使部署為 single VM 也保留兩 process（systemd / NSSM 各自 unit）。

---

## 已知限制

1. **同一 SQLite 連線**：兩個 process 同時 read SQLite（SQLite 支援多 reader + 單 writer）。background worker 才寫；8444 / 8555 都 read-only。如 background worker 寫入期間 8444 / 8555 read，平均 latency 各 +5ms，仍可接受。
2. **Session token cache 分裂**：每個 process 內 `SessionStore` 是獨立 dict — clips 同一 NVR 在 8444 process 重新 login、8555 process 也再 login 一次。優點：process restart 不互相影響。缺點：偶發兩 process 同時打 NVR 的 login threshold。Mitigation：`NVR_CLIPS_CLIENT=mock` for dev test。
3. **worker 與 web 寫入衝突風險**：目前 worker 是 solo SQLite writer；如未來需 web 端寫入，必須再做 WAL mode + connection 序列化。

---

## 變更紀律

未來若有人嘗試合併：

1. 必須先看本檔 + memory nvr-week1-day0-complete 的「Navbar 字符串設計已定案」（8444 dashboard + 8555 clips 不可隨意統一命名）
2. 必須先把 8555 + 8444 共用部分（DB / templates 命名 / features）盤點清楚
3. 必須先與 user 討論再決定

---

**為什麼這份文件重要**

這是 Week 6 #019 雙 App 隔離策略的唯一決策紀錄。新人 on-boarding 看到 codebase 若疑惑「為什麼 dashboard 跟 clips 拆兩個 app 跑」，讀本檔即可；不必再翻 roadmap §1.3。

**How to apply:**

- 部署 8555 與 8444 之間的 cross-port 連線，永遠走 `NVR_DASHBOARD_URL` / `NVR_CLIPS_URL` env，不寫死 `http://127.0.0.1:8444` / `:8555`。
- 反向代理部署時，8444 / 8555 各自獨立 process、獨立 TLS cert、獨立 health check endpoint（health 由 `GET /` 200 決定）。
- 不要嘗試單 process 合併；若要，先讀本檔 + 開 issue 討論。
