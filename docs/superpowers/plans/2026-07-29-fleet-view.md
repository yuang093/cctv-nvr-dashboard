# /fleet View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/fleet` 獨立頁顯示每台 enabled NVR 的攝影機健康概覽卡（總數 + 健康/訊號中斷/無訊號三類計數 + 狀態圓點 + 三色拼接進度條）。

**Architecture:** 複用既有 `web.db.get_wall_cameras_with_snapshots` helper（671 pytest 已涵蓋），加 `nvr_id` 選填參數做單 NVR 過濾。新增 `web/fleet.py` 聚合 NVR 清單 + 每台 cam 分類計數，30s TTL in-memory cache 避免重複查詢。模板用 Bootstrap card + inline CSS gradient 拼接條。

**Tech Stack:** Flask / Jinja2 / SQLite（既有，無新依賴）。

**Spec:** `docs/superpowers/specs/2026-07-29-fleet-view-design.md`

---

## Task 1: 加 `nvr_id` 選填參數到 `get_wall_cameras_with_snapshots`

**Files:**
- Modify: `web/db.py:get_wall_cameras_with_snapshots`（搜尋函式定義位置）
- Test: `tests/test_wall_with_snapshots.py`（既有測試檔）

- [ ] **Step 1: 寫新測試驗證 `nvr_id` filter 生效**

在 `tests/test_wall_with_snapshots.py` 加：

```python
def test_get_wall_cameras_with_snapshots_filters_by_nvr_id():
    """傳 nvr_id 參數時只回該 NVR 的 cam。"""
    import tempfile, gc
    from pathlib import Path
    from db.sqlite_writer import SqliteWriter
    from web.db import get_wall_cameras_with_snapshots

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({"id": "A", "name": "A店", "host": "1.1.1.1", "port": 8443, "username": "u", "password": "p"})
    nvrb = w.upsert_nvr({"id": "B", "name": "B店", "host": "2.2.2.2", "port": 8443, "username": "u", "password": "p"})
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    w.upsert_cameras(nvra, {
        "d1": {"name": "A大門", "connection_state": "CONNECTED"},
        "d2": {"name": "A後門", "connection_state": "CONNECTED"},
    })
    w.upsert_cameras(nvrb, {
        "d10": {"name": "B大門", "connection_state": "CONNECTED"},
    })
    w.finish_scan_run(rid, finished_at="2026-07-29T00:01:00Z", status="success",
                      stats={"total_cameras": 3, "abnormal_cameras": 0,
                             "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0})
    del w
    gc.collect()

    only_a = get_wall_cameras_with_snapshots(db_path, nvr_id=nvra)
    assert len(only_a) == 2
    assert all(c["nvr_name"] == "A店" for c in only_a)

    only_b = get_wall_cameras_with_snapshots(db_path, nvr_id=nvrb)
    assert len(only_b) == 1
    assert only_b[0]["nvr_name"] == "B店"

    # 不傳 nvr_id → 回全部（向後相容）
    all_cams = get_wall_cameras_with_snapshots(db_path)
    assert len(all_cams) == 3

    Path(db_path).unlink(missing_ok=True)
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
pytest tests/test_wall_with_snapshots.py::test_get_wall_cameras_with_snapshots_filters_by_nvr_id -v
```

預期：FAIL — `TypeError: unexpected keyword argument 'nvr_id'`。

- [ ] **Step 3: 修改 `web/db.py:get_wall_cameras_with_snapshots` 加 `nvr_id` 參數**

找到函式定義（用 grep 確認位置），在 signature 加：

```python
def get_wall_cameras_with_snapshots(
    db_path: str,
    filter_kind: str = "all",
    nvr_id: int | None = None,
) -> list[dict]:
```

然後在 SQL WHERE 條件組裝處加：

```python
extra_where = ""
extra_params: list = []
if nvr_id is not None:
    extra_where = " AND c.nvr_id = ?"
    extra_params.append(nvr_id)
```

把 `extra_where` 串接到現有 WHERE，把 `extra_params` append 到 params list。

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
pytest tests/test_wall_with_snapshots.py -v
```

預期：所有測試 PASS（含新加的 + 既有的約 10 個）。

- [ ] **Step 5: Commit**

```bash
git add web/db.py tests/test_wall_with_snapshots.py
git commit -m "feat(db): add optional nvr_id filter to get_wall_cameras_with_snapshots"
```

---

## Task 2: 寫 `web/fleet.py:get_fleet_view` 第一個測試

**Files:**
- Test: `tests/test_fleet_view.py`（新檔）
- Create: `web/fleet.py`

- [ ] **Step 1: 寫測試檔第一個 case**

```python
"""
tests/test_fleet_view.py
=========================
2026-07-29 新功能：/fleet 頁伺服器概覽。

對應 spec：docs/superpowers/specs/2026-07-29-fleet-view-design.md
"""
from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app
from web.fleet import get_fleet_view, clear_cache


@pytest.fixture
def fleet_db():
    """灌 2 台 NVR、各 NVR 不同 cam 狀態（健康 / 訊號中斷 / 無訊號）。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    w = SqliteWriter(db_path)
    nvra = w.upsert_nvr({
        "id": "NVR-A", "name": "A 辦公室", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p",
    })
    nvrb = w.upsert_nvr({
        "id": "NVR-B", "name": "B 倉庫", "host": "10.0.0.2",
        "port": 8443, "username": "u", "password": "p",
    })
    rid = w.begin_scan_run("2026-07-29T00:00:00Z")
    # A：5 cam（3 健康 + 1 訊號中斷 + 1 無訊號）
    w.upsert_cameras(nvra, {
        "a1": {"name": "A1", "connection_state": "CONNECTED"},
        "a2": {"name": "A2", "connection_state": "CONNECTED"},
        "a3": {"name": "A3", "connection_state": "CONNECTED"},
        "a4": {"name": "A4", "connection_state": "CONNECTED"},
        "a5": {"name": "A5", "connection_state": "CONNECTED"},
    })
    w.insert_events(rid, nvra, [{
        "eventId": "e1", "deviceId": "a4",
        "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
        "eventTopic": "DEVICE_VIDEO_SIGNAL_LOST",
        "occurred_at": "2026-07-29T00:00:00Z",
    }])
    w.insert_events(rid, nvra, [{
        "eventId": "e2", "deviceId": "a5",
        "eventTopics": ["STATE_LONG_FAILED"],
        "eventTopic": "STATE_LONG_FAILED",
        "occurred_at": "2026-07-29T00:00:00Z",
    }])
    # B：3 cam 全健康
    w.upsert_cameras(nvrb, {
        "b1": {"name": "B1", "connection_state": "CONNECTED"},
        "b2": {"name": "B2", "connection_state": "CONNECTED"},
        "b3": {"name": "B3", "connection_state": "CONNECTED"},
    })
    w.finish_scan_run(rid, finished_at="2026-07-29T00:01:00Z", status="success",
                      stats={"total_cameras": 8, "abnormal_cameras": 2,
                             "total_nvrs": 2, "ok_nvrs": 2, "failed_nvrs": 0})
    del w
    gc.collect()
    yield db_path
    Path(db_path).unlink(missing_ok=True)


# === 1. get_fleet_view 回傳 list，每台 NVR 一個 dict ===
def test_get_fleet_view_returns_one_dict_per_nvr(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all("name" in n and "total" in n for n in result)


# === 2. 每台 NVR 的 total = 該 NVR cam 總數 ===
def test_get_fleet_view_counts_total_cameras(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    by_name = {n["name"]: n for n in result}
    assert by_name["A 辦公室"]["total"] == 5
    assert by_name["B 倉庫"]["total"] == 3


# === 3. 每台 NVR 三類計數正確 ===
def test_get_fleet_view_counts_health_categories(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    by_name = {n["name"]: n for n in result}
    a = by_name["A 辦公室"]
    assert a["healthy"] == 3
    assert a["signal_lost"] == 1
    assert a["no_signal"] == 1
    b = by_name["B 倉庫"]
    assert b["healthy"] == 3
    assert b["signal_lost"] == 0
    assert b["no_signal"] == 0


# === 4. status 推論正確（critical / degraded / ok）===
def test_get_fleet_view_status_inference(fleet_db):
    clear_cache()
    result = get_fleet_view(fleet_db)
    by_name = {n["name"]: n for n in result}
    assert by_name["A 辦公室"]["status"] == "critical"  # 有 no_signal
    assert by_name["B 倉庫"]["status"] == "ok"


# === 5. 0 台 NVR 回空 list ===
def test_get_fleet_view_empty_db_returns_empty_list():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    SqliteWriter(db_path)
    clear_cache()
    assert get_fleet_view(db_path) == []
    Path(db_path).unlink(missing_ok=True)


# === 6. 30s TTL 內走 cache（同一 db_path 兩次呼叫，第一次會算；force_refresh=False 時第二次走 cache）===
def test_get_fleet_view_uses_cache_within_ttl(monkeypatch, fleet_db):
    """兩次呼叫 get_fleet_view 30s 內，第二次的 timestamp 應等於第一次（走 cache）。"""
    from web import fleet

    clear_cache()
    t = [1000.0]
    monkeypatch.setattr(fleet.time, "time", lambda: t[0])

    # 第一次：算
    fleet.get_fleet_view(fleet_db, force_refresh=True)
    cached_ts_after_first = fleet._CACHE["ts"]

    # 第二次：25 秒後（30s TTL 內）→ 應走 cache
    t[0] = 1025.0
    fleet.get_fleet_view(fleet_db)
    assert fleet._CACHE["ts"] == cached_ts_after_first, "應走 cache（ts 未更新）"

    # 第三次：40 秒後（超過 30s TTL）→ 應重算
    t[0] = 1065.0
    fleet.get_fleet_view(fleet_db)
    assert fleet._CACHE["ts"] == 1065.0, "應重算（ts 已更新）"


# === 7. 單台 NVR 的 get_wall_cameras_with_snapshots 拋例外時，其他台仍正常 ===
def test_get_fleet_view_partial_failure_isolated(monkeypatch, fleet_db):
    """一台 NVR 的 helper 拋例外，該台 status='unknown'，其他台仍正常。"""
    from web import fleet

    def boom(*args, **kwargs):
        if kwargs.get("nvr_id") == 1:
            raise RuntimeError("simulated DB error")
        return []  # 其他台回空（不會影響測試重點）

    monkeypatch.setattr(fleet, "get_wall_cameras_with_snapshots", boom)
    clear_cache()

    result = fleet.get_fleet_view(fleet_db, force_refresh=True)
    statuses = sorted(n["status"] for n in result)
    assert "unknown" in statuses, "至少一台應為 unknown"


# === 8. /fleet route 回 200 並含 NVR 名稱 ===
def test_fleet_route_returns_200_and_renders_nvr_names(fleet_db):
    clear_cache()
    app = create_app(db_path=fleet_db)
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.get("/fleet")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "A 辦公室" in body
    assert "B 倉庫" in body
```

- [ ] **Step 2: 跑測試確認 RED**

```bash
pytest tests/test_fleet_view.py -v
```

預期：8 failed（ModuleNotFoundError: No module named 'web.fleet' 或 AttributeError）。

- [ ] **Step 3: 建立 `web/fleet.py` 實作**

```python
"""
web/fleet.py
============
2026-07-29 新功能：/fleet 頁伺服器概覽資料聚合。

對應 spec：docs/superpowers/specs/2026-07-29-fleet-view-design.md
"""
from __future__ import annotations

import logging
import time

from web.db import get_wall_cameras_with_snapshots, list_enabled_nvrs

log = logging.getLogger(__name__)

_CACHE: dict = {"data": None, "ts": 0.0}
_TTL_SECONDS = 30.0


def get_fleet_view(db_path: str, *, force_refresh: bool = False) -> list[dict]:
    """回傳每台 enabled NVR 的概覽 dict list。

    30s TTL in-memory cache。force_refresh=True 強制重算（測試用）。
    單台 NVR 例外不中斷整批；該台 status='unknown'，其他台正常。
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
            log.warning("fleet view failed for nvr_id=%s: %s", nvr["id"], exc)
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
    """測試 / NVR 設定變動後可呼叫清掉 cache。"""
    _CACHE["data"] = None
    _CACHE["ts"] = 0.0
```

- [ ] **Step 4: 跑測試確認 GREEN**

```bash
pytest tests/test_fleet_view.py -v
```

預期：8 passed（route /fleet 還沒建會 fail test #8 — 預期）。

只前 7 個應 pass，第 8 個會 fail（No module 'web.app' route /fleet — 預期，Task 3 處理）。

```bash
pytest tests/test_fleet_view.py::test_get_fleet_view_returns_one_dict_per_nvr \
              tests/test_fleet_view.py::test_get_fleet_view_counts_total_cameras \
              tests/test_fleet_view.py::test_get_fleet_view_counts_health_categories \
              tests/test_fleet_view.py::test_get_fleet_view_status_inference \
              tests/test_fleet_view.py::test_get_fleet_view_empty_db_returns_empty_list \
              tests/test_fleet_view.py::test_get_fleet_view_uses_cache_within_ttl \
              tests/test_fleet_view.py::test_get_fleet_view_partial_failure_isolated -v
```

預期：7 passed。

- [ ] **Step 5: Commit**

```bash
git add web/fleet.py tests/test_fleet_view.py
git commit -m "feat(fleet): add get_fleet_view with 30s TTL cache + partial failure isolation"
```

---

## Task 3: 加 `/fleet` route 到 `web/app.py`

**Files:**
- Modify: `web/app.py`（在 `/` route 附近加 `/fleet`）

- [ ] **Step 1: 跑 route 測試確認 RED**

```bash
pytest tests/test_fleet_view.py::test_fleet_route_returns_200_and_renders_nvr_names -v
```

預期：FAIL — 404 Not Found。

- [ ] **Step 2: 在 `web/app.py` 加 route**

找 import 區加：

```python
from web.fleet import get_fleet_view
```

找 `/` dashboard route 附近加：

```python
    @app.route("/fleet")
    def fleet():
        nvrs = get_fleet_view(_get_db_path(app))
        total_cams = sum(n["total"] for n in nvrs)
        return render_template(
            "fleet.html",
            nvrs=nvrs,
            total_cams=total_cams,
        )
```

（`_get_db_path(app)` 用現有的 helper — 跟 `/wall` route 一致。）

- [ ] **Step 3: 跑測試確認 GREEN**

```bash
pytest tests/test_fleet_view.py -v
```

預期：8 passed（Task 2 第 8 個測試現在 pass）。

- [ ] **Step 4: 跑全套 pytest 確認沒破**

```bash
pytest -q
```

預期：684 passed（676 baseline + 8 新測試）。

- [ ] **Step 5: Commit**

```bash
git add web/app.py
git commit -m "feat(web): add /fleet route returning get_fleet_view"
```

---

## Task 4: 建 `web/templates/fleet.html`

**Files:**
- Create: `web/templates/fleet.html`

- [ ] **Step 1: 建立模板**

```html
{% extends "base.html" %}
{% block title %}伺服器總覽 · NVR Scanner{% endblock %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
    <h1 class="mb-0">📡 伺服器總覽</h1>
    <span class="badge bg-secondary">
        共 {{ nvrs | length }} 台伺服器 / {{ total_cams }} 台攝影機
    </span>
</div>

{% if nvrs %}
<div class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3">
    {% for n in nvrs %}
    <div class="col">
        <div class="card fleet-card h-100">
            <div class="card-body">
                <h5 class="d-flex align-items-center">
                    <span class="fleet-dot
                        {% if n.status == 'ok' %}bg-success
                        {% elif n.status == 'degraded' %}bg-warning
                        {% elif n.status == 'critical' %}bg-danger
                        {% else %}bg-secondary{% endif %}"></span>
                    <span class="text-truncate" title="{{ n.name }}">{{ n.name }}</span>
                </h5>
                <small class="text-muted">{{ n.host }}:{{ n.port }}</small>

                {# 三色拼接進度條（綠/琥珀/紅）#}
                <div class="fleet-bar mt-3">
                    {% if n.total > 0 %}
                        <div class="fleet-bar-ok" style="width: {{ (n.healthy / n.total * 100) | round(1) }}%"></div>
                        <div class="fleet-bar-warn" style="width: {{ (n.signal_lost / n.total * 100) | round(1) }}%"></div>
                        <div class="fleet-bar-crit" style="width: {{ (n.no_signal / n.total * 100) | round(1) }}%"></div>
                    {% endif %}
                </div>

                <div class="small mt-2">
                    <span class="text-success">✅ 健康 {{ n.healthy }}</span>
                    <span class="ms-2 text-warning">⚠ 訊號中斷 {{ n.signal_lost }}</span>
                    <span class="ms-2 text-danger">✗ 無訊號 {{ n.no_signal }}</span>
                </div>
            </div>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="alert alert-info">
    尚無啟用的 NVR。請到 <a href="{{ url_for('nvrs_list') }}">NVR 清單</a> 新增。
</div>
{% endif %}

<style>
.fleet-card {
    transition: transform .15s ease, box-shadow .15s ease;
    overflow: hidden;
}
.fleet-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 0.25rem 0.75rem rgba(0,0,0,0.15);
}
.fleet-dot {
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 0 4px rgba(0,0,0,0.3);
    margin-right: 8px;
    flex-shrink: 0;
}
.fleet-bar {
    display: flex;
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
    background: #e2e8f0;
}
.fleet-bar-ok { background: #198754; }
.fleet-bar-warn { background: #ffc107; }
.fleet-bar-crit { background: #dc3545; }

{% if dark %}
.fleet-card {
    background: #1a1a2e;
    border-color: #334155;
}
.fleet-card:hover {
    box-shadow: 0 0.25rem 0.75rem rgba(0,0,0,0.5);
}
.fleet-bar {
    background: #0f172a;
}
.fleet-dot {
    border-color: #1a1a2e;
}
{% endif %}
</style>
{% endblock %}
```

- [ ] **Step 2: 確認 8 個 fleet 測試 + 既有 wall 測試仍綠**

```bash
pytest tests/test_fleet_view.py tests/test_wall_routes.py tests/test_wall_with_snapshots.py -v
```

預期：全綠。

- [ ] **Step 3: Commit**

```bash
git add web/templates/fleet.html
git commit -m "feat(web): add fleet.html template with cards + tri-color bar + dark mode"
```

---

## Task 5: Sidebar 加 `/fleet` 連結

**Files:**
- Modify: `web/templates/base.html`

- [ ] **Step 1: 跑驗證確認現有 sidebar 沒有 fleet 連結**

```bash
curl -sS http://127.0.0.1:8444/ | grep -c "fleet"
```

預期：0（server 已重啟）。

- [ ] **Step 2: 在 `web/templates/base.html` sidebar 加連結**

找到現有「📹 相機牆」或相鄰 nav item，把「📡 伺服器總覽」放在「相機牆」上方：

```html
<li class="nav-item">
    <a href="{{ url_for('fleet') }}" class="nav-link">
        <span class="nav-icon">📡</span> 伺服器總覽
    </a>
</li>
```

（具體 class 名稱依 base.html 既有結構調整；該檔已有 sidebar nav pattern。）

- [ ] **Step 3: 跑全套 pytest 確認沒破**

```bash
pytest -q
```

預期：684 passed。

- [ ] **Step 4: 重啟 8444 + curl 驗證**

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8444 -ErrorAction SilentlyContinue | Select-Object OwningProcess | Stop-Process -Force"
sleep 1
PYTHONPATH=. nohup python web/app.py > nvr_web.out 2>&1 &
sleep 4
curl -sS http://127.0.0.1:8444/fleet | grep -c "A 辦公室"
```

預期：1（看到 NVR 名稱 → template 渲染成功）。

- [ ] **Step 5: Playwright 截圖驗證**

用 `mcp__plugin_chrome-devtools-mcp_chrome-devtools__new_page` 開 `http://127.0.0.1:8444/fleet`，`take_screenshot` 存 `C:\cc\NVR\fleet-real.png`。

檢查截圖：
- 兩張卡（A 辦公室 / B 倉庫）
- 紅/綠圓點分別對應 critical / ok
- 三色拼接條
- 三個計數 ✅ 健康 / ⚠ 訊號中斷 / ✗ 無訊號

- [ ] **Step 6: Commit**

```bash
git add web/templates/base.html
git commit -m "feat(web): add /fleet link to sidebar"
```

---

## Verification Checklist

- [ ] 8 個 fleet 測試全綠
- [ ] 既有的 676 個測試沒破（總計 684）
- [ ] `get_wall_cameras_with_snapshots` 新增 nvr_id filter 向後相容（Task 1 測試）
- [ ] /fleet route 200 + 渲染 NVR 名稱
- [ ] Playwright 截圖：兩張卡版面對齊 021.PNG
- [ ] Sidebar 連結生效
- [ ] 30s cache TTL 正確（Task 2 test #6）
- [ ] 單台失敗隔離（Task 2 test #7）

## Self-Review（對 spec 逐項檢查）

| Spec 區段 | 對應 Task |
|---|---|
| 架構（4 元件） | Task 1 + 2 + 3 + 4 |
| `web/fleet.py` + 30s cache | Task 2 |
| `get_wall_cameras_with_snapshots` 加 nvr_id | Task 1 |
| `/fleet` route | Task 3 |
| `web/templates/fleet.html` | Task 4 |
| Sidebar 整合 | Task 5 |
| 8 個測試 | Task 2 + 3 |
| Dark mode CSS | Task 4（template 內 inline） |
| 錯誤處理（單台隔離 + cache） | Task 2 test #6、#7 |
| 30s TTL 風險緩解 | Task 2 test #6 |

無遺漏。準備執行。
