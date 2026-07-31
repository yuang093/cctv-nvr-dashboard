# Spec B：/fleet 相機健康分布圖表（2026-07-31）

> **對應藍圖**：021.PNG「相機健康 / 雲端覆蓋 / 廢牌分布」**只實作第一項**（最小可行）。
> 完整藍圖參考 `docs/superpowers/specs/2026-07-29-fleet-view-design.md`。

## Context

`/fleet` 頁（Spec A，2026-07-29 實作）目前只顯示每台 NVR 的「伺服器卡」（總相機數 / 在線 / 離線 / 故障）。**缺少跨 NVR 合計的相機健康分布總覽** — user 需要一眼看出「目前全體相機有多少比例正常 / 訊號斷 / 無訊號」。

021.PNG 藍圖列出三項分布（相機健康 / 雲端覆蓋 / 廢牌分布）。**本次只做第一項**（最小可行），其他兩項留 Spec B+ 或 Spec C。

## 設計

### 1. 視覺

`/fleet` 頁**頂部**（伺服器卡 grid 之上）新增一個 row：

```
┌─────────────────────────────┐  ┌─────────────────────────────┐
│  相機健康分布                 │  │ 文字 legend                 │
│                              │  │                              │
│        ╱─────╲               │  │ ● 在線 (online)        2   │
│       ╱       ╲              │  │   訊號正常                  │
│      │  donut  │             │  │                              │
│       ╲       ╱              │  │ ● 訊號斷 (signal_lost)  0   │
│        ╲─────╱               │  │   攝影機仍在 但無影像        │
│                              │  │                              │
│   總計 2 台 cam               │  │ ● 無訊號 (no_signal)    0   │
│                              │  │   攝影機 disconnect        │
│                              │  │                              │
│                              │  │ ⚠ ghost cam 1 台（不算入）  │
└─────────────────────────────┘  └─────────────────────────────┘
```

技術：Chart.js 4.x CDN（v4.4.0，stable）。

### 2. 資料來源

`web/fleet.py` 新增 `get_camera_health_distribution(db_path)`：

```python
def get_camera_health_distribution(db_path: str) -> dict:
    """跨 NVR 合計 cam 的健康分布（過濾 ghost cam）。

    Returns:
        {
            "online": int,        # connection=CONNECTED 且無 signal_lost/no_signal 事件
            "signal_lost": int,   # 有訊號斷事件（訊號不見但 cam 還在）
            "no_signal": int,     # cam 斷線 / disconnect
            "total": int,         # = online + signal_lost + no_signal
            "ghost_count": int,   # 被過濾掉的 ghost cam 數（顯示在 legend）
        }
    """
```

複用 `get_wall_cameras_with_snapshots(db_path, filter_kind="all")` 取得所有 cam（含 category），計算 `online / signal_lost / no_signal`。

**Ghost 過濾**：line 582（`get_wall_cameras_with_snapshots`）已自動過濾。**不再重覆過濾**。

### 3. /fleet 頁修改

- 新增 card：「相機健康分布」
- donut canvas
- legend 顯示計數 + 定義（讓 user 知道三類差異）
- ghost 數量在 legend 下方小字（「⚠ N 台 ghost cam 不計入」）

### 4. 30s cache 沿用

`get_camera_health_distribution` **不自己 cached** — 因為 `get_wall_cameras_with_snapshots` 每次都讀 DB（無 cache），分佈即時反映。**即時更新**。

（vs. `get_fleet_view` 需要 cache 是因為它呼叫多次 `get_wall_cameras_with_snapshots` per NVR）

### 5. TDD

3 個測試：
1. `test_health_distribution_basic` — 3 cam（1 online / 1 signal_lost / 1 no_signal）→ 1/1/1
2. `test_health_distribution_excludes_ghost` — 加 1 ghost cam → 仍 1/1/1，且 ghost_count=1
3. `test_health_distribution_empty` — 沒 cam → 0/0/0/0/0（donut 顯示「無資料」）

### 6. 不做的事（scope 控制）

- ❌ 不做「雲端覆蓋」圖（has thumbnail / no thumbnail）
- ❌ 不做「廢牌分布」圖（藍圖術語不清楚）
- ❌ 不做 per-NVR stacked bar（單台 NVR 沒意義）
- ❌ 不寫進 `get_fleet_view` — 兩個獨立函數，donut 即時更新
- ❌ 不加 Chart.js 到 base.html（CDN 只在 fleet.html 引入）

## 檔案改動

### 新增

- `tests/test_fleet_camera_health_distribution.py` — 3 個測試

### 修改

- `web/fleet.py` — 加 `get_camera_health_distribution()` (~30 行)
- `web/app.py` — `/fleet` route 取得 distribution dict 傳給 template
- `web/templates/fleet.html` — 加 Chart.js CDN + canvas + legend

## 驗證

1. `pytest -q`：733 → 736（+3）
2. `curl http://127.0.0.1:8444/fleet` 確認頁面有 chart canvas
3. Playwright 截圖確認 donut 顯示正確比例
4. 確認 /fleet 已有 30s cache 行為不變

## 連結

- `docs/superpowers/specs/2026-07-29-fleet-view-design.md` — Spec A 主文件
- `web/fleet.py` — 既有（會擴展）
- `web/templates/fleet.html` — 既有（會擴展）
