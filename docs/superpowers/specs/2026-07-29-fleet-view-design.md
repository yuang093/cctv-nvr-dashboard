# Spec A：伺服器總覽頁 /fleet（2026-07-29）

> 系列開頭：本 spec 是 021.PNG「伺服器、健康圖表與 Fleet Pulse」藍圖的 **第一階段**。
> 後續 Spec B（圖表）、Spec C（即時 metrics）分批進行。

## Context

user 提供 `C:\cc\NVR\021.PNG` mockup，描述 4 項 dashboard 新功能：
1. ✅ 伺服器卡（本 spec）
2. 相機分布圖表（Spec B）
3. Fleet Pulse 即時 CPU/RAM（Spec C）
4. 系統狀態（Spec C 合併）

**現況**：Web UI 完全沒有「多 NVR 概覽」獨立頁，只能在 `/nvrs` 看設定清單、`/wall` 看全部 cam 縮圖、dashboard 看掃描統計。管理員需要一眼掌握「6 台 NVR 各自健康狀態」時，必須點進 `/wall` 自己數。

**目標**：新增 `/fleet` 獨立頁，顯示每台 enabled NVR 一張卡，卡上列出該 NVR 攝影機總數、健康/訊號中斷/無訊號三類計數，與狀態圓點 + 拼接進度條。

**不做**（避免 scope creep）：
- ❌ 即時 polling / WS（本 spec 只 page load 算一次）
- ❌ 點擊跳詳情（user 明確選「純顯示不可點」）
- ❌ 圖表、CPU/RAM metrics、轉碼活動（留 Spec B/C）

## User Decisions（brainstorming session 結論）

| 問題 | 決定 |
|---|---|
| 頁面位置 | 獨立 `/fleet` 頁（dashboard 加連結進去） |
| 卡上資訊密度 | 卡內細項計數（健康 / 訊號中斷 / 無訊號 各自數字） |
| 點擊行為 | 純顯示不可點 |

## Design

### 架構

```
Browser
  GET /fleet
    ↓
web/app.py:@app.route('/fleet')
    ↓
web/fleet.py:get_fleet_view(db_path)   ← NEW
    ├─ 30s TTL in-memory cache
    ├─ list_enabled_nvrs(db_path)        (既有)
    └─ get_wall_cameras_with_snapshots(  (既有 + 加 nvr_id filter)
         db_path, filter_kind="all", nvr_id=nvr["id"])
    ↓
web/templates/fleet.html  ← NEW
```

### 元件

| 元件 | 職責 | 狀態 |
|---|---|---|
| `web/fleet.py` | 聚合 NVR 清單 + 每台 cam 分類計數；30s TTL cache | NEW |
| `web/templates/fleet.html` | 卡片版面、SVG 拼接進度條、dark mode CSS | NEW |
| `web/db.py:list_enabled_nvrs` | 列出 enabled NVR | 既有，不改 |
| `web/db.py:get_wall_cameras_with_snapshots` | 每台 cam 帶 category + snapshot；**加 `nvr_id` 選填參數** | 修改（向後相容，預設 None = 不過濾） |
| `web/app.py:@app.route('/fleet')` | 組裝 view → render template | NEW |
| `web/templates/base.html` 或 `dashboard.html` | 加 `/fleet` 連結到 sidebar / dashboard | 既有，小改 |

### 資料模型

`/fleet` 不存新資料；純讀既有 `nvr_servers` + `cameras` + `events`（透過既有 `get_wall_cameras_with_snapshots`）。

### 關鍵函式

```python
# web/fleet.py
import time
from typing import Optional
from web.db import list_enabled_nvrs, get_wall_cameras_with_snapshots

_CACHE = {"data": None, "ts": 0.0}
_TTL_SECONDS = 30.0


def get_fleet_view(db_path: str, *, force_refresh: bool = False) -> list[dict]:
    """回傳每台 enabled NVR 的概覽 dict list。

    Returns:
        [{
            "nvr_id": int,
            "name": str,
            "host": str,
            "port": int,
            "total": int,           # 該 NVR cam 總數
            "healthy": int,         # category == "online"
            "signal_lost": int,     # category == "signal_lost"
            "no_signal": int,       # category == "no_signal"
            "status": str,          # "ok" | "degraded" | "critical"
        }, ...]

    Notes:
        - 30s TTL：避免 /fleet 高頻重複掃 DB；force_refresh=True 可強制重算（測試用）
        - 任何單台 NVR 的例外不中斷整批，該台回 status="unknown" 並 log warning
    """
    now = time.time()
    if not force_refresh and _CACHE["data"] is not None and now - _CACHE["ts"] < _TTL_SECONDS:
        return _CACHE["data"]

    nvrs = list_enabled_nvrs(db_path)
    out: list[dict] = []
    for nvr in nvrs:
        try:
            cams = get_wall_cameras_with_snapshots(
                db_path, filter_kind="all", nvr_id=nvr["id"]
            )
            healthy = sum(1 for c in cams if c["category"] == "online")
            signal_lost = sum(1 for c in cams if c["category"] == "signal_lost")
            no_signal = sum(1 for c in cams if c["category"] == "no_signal")
            total = len(cams)
            if no_signal > 0:
                status = "critical"
            elif signal_lost > 0:
                status = "degraded"
            else:
                status = "ok"
        except Exception as exc:
            # 單台失敗不中斷整批；記 warning
            import logging
            logging.getLogger(__name__).warning(
                "fleet view failed for nvr_id=%s: %s", nvr["id"], exc
            )
            cams = []
            healthy = signal_lost = no_signal = total = 0
            status = "unknown"

        out.append({
            "nvr_id": nvr["id"],
            "name": nvr["name"],
            "host": nvr["host"],
            "port": nvr.get("port", 8443),
            "total": total,
            "healthy": healthy,
            "signal_lost": signal_lost,
            "no_signal": no_signal,
            "status": status,
        })

    _CACHE["data"] = out
    _CACHE["ts"] = now
    return out


def clear_cache() -> None:
    """測試 / NVR 設定變動後可呼叫。"""
    _CACHE["data"] = None
    _CACHE["ts"] = 0.0
```

### Template 結構（`web/templates/fleet.html`）

```html
{% extends "base.html" %}
{% block title %}伺服器總覽 · NVR Scanner{% endblock %}
{% block content %}
<h1>📡 伺服器總覽</h1>
<p class="text-muted">
    共 {{ nvrs | length }} 台伺服器 / {{ total_cams }} 台攝影機
</p>

{% if nvrs %}
<div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3">
    {% for n in nvrs %}
    <div class="col">
        <div class="card fleet-card h-100">
            <div class="card-body">
                <h5>
                    <span class="fleet-dot bg-{% if n.status == 'ok' %}success{% elif n.status == 'degraded' %}warning{% elif n.status == 'critical' %}danger{% else %}secondary{% endif %}"></span>
                    {{ n.name }}
                </h5>
                <small class="text-muted">{{ n.host }}:{{ n.port }}</small>

                {# 三色拼接進度條（綠/琥珀/紅）#}
                <div class="fleet-bar mt-3">
                    {% if n.total > 0 %}
                        <div class="fleet-bar-ok" style="width: {{ (n.healthy / n.total * 100) if n.total else 0 }}%"></div>
                        <div class="fleet-bar-warn" style="width: {{ (n.signal_lost / n.total * 100) if n.total else 0 }}%"></div>
                        <div class="fleet-bar-crit" style="width: {{ (n.no_signal / n.total * 100) if n.total else 0 }}%"></div>
                    {% endif %}
                </div>

                <div class="small mt-2">
                    <span class="text-success">✅ 健康 {{ n.healthy }}</span>
                    <span class="text-warning">⚠ 訊號中斷 {{ n.signal_lost }}</span>
                    <span class="text-danger">✗ 無訊號 {{ n.no_signal }}</span>
                </div>
            </div>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="alert alert-info">尚無啟用的 NVR。請到 <a href="{{ url_for('nvrs_list') }}">NVR 清單</a> 新增。</div>
{% endif %}
{% endblock %}
```

### `get_wall_cameras_with_snapshots` 修改（向後相容）

```python
def get_wall_cameras_with_snapshots(
    db_path: str,
    filter_kind: str = "all",
    nvr_id: int | None = None,  # ← 新增（None = 不過濾）
) -> list[dict]:
    ...
    # 注意：原 SQL 沒既有 WHERE，所以這裡用 WHERE（不是 AND）
    extra_where = "" if nvr_id is None else " WHERE c.nvr_id = ?"
    extra_params = [] if nvr_id is None else [nvr_id]
    ...
```

舊呼叫端（`/wall` route）完全不傳 → 行為不變。

### Dark Mode

跟現有 8555 /wall 一致：模板內 `{% if dark %}<style>...</style>{% endif %}` 寫一份 override：
- card 背景 `#1a1a2e`、border `#334155`
- 進度條顏色不變（綠/琥珀/紅在 dark 仍清楚）
- 小字 text-muted 改 `#94a3b8`

### Sidebar 整合

在 `web/templates/base.html` 的 sidebar nav 加一條：
```html
<li><a href="{{ url_for('fleet') }}" class="...">📡 伺服器總覽</a></li>
```
位置：放在「📹 相機牆」上方（fleet 是更高層次的概覽）。

## Testing

### 新測試檔 `tests/test_fleet_view.py`（預期 +7 個測試）

| 測試 | 驗證 |
|---|---|
| `test_fleet_route_returns_200` | GET /fleet 回 200 |
| `test_fleet_renders_nvr_names_and_hosts` | 模板含 NVR 名稱 + host |
| `test_fleet_renders_summary_line` | 標題副標題含「共 N 台伺服器 / M 台攝影機」 |
| `test_fleet_card_shows_three_counts` | 卡內顯示「健康 / 訊號中斷 / 無訊號」三個計數 |
| `test_fleet_status_dot_color_matches_state` | ok → bg-success、degraded → bg-warning、critical → bg-danger |
| `test_fleet_handles_no_nvrs` | 0 台 NVR 顯示空態 alert |
| `test_fleet_cache_30s_ttl` | 第二次呼叫 30s 內走 cache（mock time.time） |
| `test_fleet_partial_failure_isolated` | 單台 NVR 的 get_wall_cameras_with_snapshots 拋例外，其他台仍正常顯示 |

### 既測試更新

`tests/test_wall_routes.py`：無變動（`get_wall_cameras_with_snapshots` 向後相容）。

### pytest 預期

baseline 676 → **+8 個新測試 = 684 全綠**。

## Risks & Mitigations

| 風險 | 緩解 |
|---|---|
| 30s TTL 內 NVR 異常無法即時反映 | user 接受「最多 30s 延遲」；如需即時可手動 reload；Spec C 才有 polling |
| 6+ 台 NVR 時頁面變長 | row-cols-lg-3 響應式；後續可加分頁（user 之前說過「未來要分頁」） |
| `get_wall_cameras_with_snapshots` 加 `nvr_id` 參數風險 | 預設 None，向後相容；既有 6+ 個測試覆蓋 |
| 進度條 0 寬度時破版 | template 加 `{% if n.total > 0 %}` 守衛 |
| Cache 測試 flaky（依賴真實時間） | mock `time.time` 強制設值 |

## Verification Steps

1. **語法**：`python -m py_compile web/fleet.py web/app.py web/db.py`
2. **測試**：`pytest tests/test_fleet_view.py -v` 看到 8 passed
3. **全套**：`pytest -q` 看到 684 passed
4. **重啟 8444**：`powershell Stop-Process` + `python web/app.py &`
5. **curl**：`curl http://127.0.0.1:8444/fleet` 看 HTML 含 NVR 名稱
6. **Playwright**：截圖 `/fleet`，確認卡片版面對齊 021.PNG

## 連結

- 021.PNG：`C:\cc\NVR\021.PNG`（mockup 來源）
- 既有 helper：`web/db.py:get_wall_cameras_with_snapshots`（+8 tests 涵蓋）
- 既有 page pattern：`web/templates/wall.html`（021.PNG 實作風格對齊）
- 後續 Spec B：相機分布圖表
- 後續 Spec C：Fleet Pulse + 系統狀態
