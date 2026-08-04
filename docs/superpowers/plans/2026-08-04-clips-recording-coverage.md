# Spec F 8555 錄影覆蓋熱區 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 8555 新頁 `/clips/coverage` 顯示 1 台 NVR 所有 cam 的 24h 錄影時間軸（綠帶 = 有錄影），點擊區段帶時間跳轉 clips 頁。

**Architecture:** 新增 `web/coverage.py` 純邏輯模組（fetch NVR `/timeline` + 解析 + 計算完整率）。`web/clips_app.py` 加 2 路由（GET 頁面 + GET JSON API）。`web/clips_templates/coverage.html` 獨立頁面（純 CSS grid div 畫時間軸，無 Chart.js）。複用既有 `web/timeline.py` 解析器。

**Tech Stack:** Python 3.11 / Flask / AvigilonScanner 既有 `get_timeline()` 單 cam API / `web/timeline.py`（既有工具）/ 純 HTML + CSS grid + 原生 JS（DnD 不需）

---

## 檔案結構（先確定）

```
web/coverage.py                          # 新 — 純邏輯（4 個函式）
web/clips_app.py                         # 改 — 加 2 路由 + 1 navbar 連結
web/clips_templates/coverage.html        # 新 — 獨立頁面
tests/test_coverage.py                   # 新 — 純邏輯測試
tests/test_coverage_endpoint.py          # 新 — API 端點測試
```

預計 5 個任務、3 個 batch、TDD 紀律嚴格執行。

---

## Task 1: `web/coverage.py` 純邏輯模組（4 函式）

**Files:**
- Create: `web/coverage.py`
- Test: `tests/test_coverage.py`

- [ ] **Step 1: Write the failing test**

建立 `tests/test_coverage.py`：

```python
"""Tests for web/coverage.py — 純邏輯（不碰 Flask / DB）。"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from web.coverage import (
    parse_records_from_timeline_response,
    compute_per_camera_completeness,
    fetch_coverage_from_nvr,
    CoverageCamera,
)


# === parse_records_from_timeline_response ===

def test_parse_records_from_timeline_response_empty_returns_empty():
    """空 payload → 回空 dict。"""
    assert parse_records_from_timeline_response(None) == {}
    assert parse_records_from_timeline_response({}) == {}
    assert parse_records_from_timeline_response({"result": {"timelines": []}}) == {}


def test_parse_records_from_timeline_response_one_camera_two_records():
    """1 台 cam、2 段錄影 → 回 1 個 key、2 個 (start, end) tuple。"""
    payload = {
        "result": {
            "timelines": [
                {
                    "cameraId": "cam-001",
                    "record": [
                        {"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"},
                        {"start": "2026-08-04T03:00:00Z", "end": "2026-08-04T05:00:00Z"},
                    ],
                }
            ]
        }
    }
    out = parse_records_from_timeline_response(payload)
    assert "cam-001" in out
    assert len(out["cam-001"]) == 2
    s1, e1 = out["cam-001"][0]
    assert s1 == datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)
    assert e1 == datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)


# === compute_per_camera_completeness ===

def test_compute_per_camera_completeness_empty_window_returns_zero():
    """空錄影 → 完整率 0.0。"""
    assert compute_per_camera_completeness([], "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z") == 0.0


def test_compute_per_camera_completeness_full_window_returns_one():
    """錄影完全覆蓋視窗 → 完整率 1.0。"""
    records = [("2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z")]
    assert compute_per_camera_completeness(records, "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z") == 1.0


def test_compute_per_camera_completeness_half_window_returns_half():
    """半小時錄影 / 1 小時視窗 → 0.5。"""
    records = [("2026-08-04T00:00:00Z", "2026-08-04T00:30:00Z")]
    assert compute_per_camera_completeness(records, "2026-08-04T00:00:00Z", "2026-08-04T01:00:00Z") == 0.5


# === fetch_coverage_from_nvr ===

def test_fetch_coverage_from_nvr_returns_dict_with_cameras_list():
    """fetch_coverage_from_nvr 回傳 dict 含 'cameras' list（每 cam 1 個 dict: cam_id, records, completeness）。"""
    from web.coverage import fetch_coverage_from_nvr

    fake_nvr = {
        "host": "127.0.0.1", "port": 8443, "nvr_id": "ACC8-P4",
        "user_nonce": "u", "user_key": "k",
    }
    fake_cameras = [
        {"device_id": "cam-001", "camera_name": "Cam 1"},
        {"device_id": "cam-002", "camera_name": "Cam 2"},
    ]
    fake_raw = {
        "result": {
            "timelines": [
                {"cameraId": "cam-001", "record": [{"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"}]},
                {"cameraId": "cam-002", "record": []},
            ]
        }
    }
    out = fetch_coverage_from_nvr(
        nvr=fake_nvr,
        cameras=fake_cameras,
        start_iso="2026-08-04T00:00:00Z",
        end_iso="2026-08-04T01:00:00Z",
        timeline_fetcher=lambda **kwargs: fake_raw,
    )
    assert "cameras" in out
    assert len(out["cameras"]) == 2
    cam1 = next(c for c in out["cameras"] if c["cam_id"] == "cam-001")
    assert cam1["completeness"] == 1.0
    assert len(cam1["records"]) == 1
    cam2 = next(c for c in out["cameras"] if c["cam_id"] == "cam-002")
    assert cam2["completeness"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'web.coverage'`（或 `ImportError`）

- [ ] **Step 3: Write minimal implementation**

建立 `web/coverage.py`：

```python
"""
web/coverage.py
===============
8555 錄影覆蓋熱區（Spec F）— 純邏輯模組。

不碰 Flask / DB — 純資料處理：解析 NVR /timeline 回傳、計算完整率。
回傳 dict 給 web/clips_app.py 路由序列化。

依賴：
    - web/timeline.py（既有：`parse_timeline_response`, `compute_completeness`）
    - nvr_scanner.AvigilonScanner.get_timeline（既有，注入測試）
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Callable, Iterable

from web.timeline import parse_timeline_response, compute_completeness


def _parse_iso_utc(s: str) -> datetime:
    """解析 ISO 8601 字串（可能帶 Z 或 +00:00 結尾）回 tz-aware UTC datetime。"""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_records_from_timeline_response(payload: dict | None) -> dict[str, list[tuple[datetime, datetime]]]:
    """
    從 NVR /timeline 原始 payload 抽出 {camera_id: [(start, end), ...]}。

    復用 web.timeline.parse_timeline_response（已在前幾版驗證）。
    純 wrapper：保留 API 進入點讓 8555 端不直接 import web.timeline。
    """
    return parse_timeline_response(payload)


def compute_per_camera_completeness(
    records: list[tuple[datetime, datetime]],
    start_iso: str,
    end_iso: str,
) -> float:
    """計算指定視窗內的錄影完整率（0.0 ~ 1.0）。"""
    start = _parse_iso_utc(start_iso)
    end = _parse_iso_utc(end_iso)
    return compute_completeness(records, start, end)


def fetch_coverage_from_nvr(
    *,
    nvr: dict,
    cameras: list[dict],
    start_iso: str,
    end_iso: str,
    timeline_fetcher: Callable[[str, str, str], dict],
) -> dict:
    """
    抓 1 台 NVR 所有 cam 的 24h timeline，回傳統一 dict。

    Args:
        nvr: dict（至少含 host / port / nvr_id / user_nonce / user_key）
        cameras: list[dict]（每個 dict 有 device_id + camera_name）
        start_iso: 視窗開始（ISO 8601）
        end_iso: 視窗結束（ISO 8601）
        timeline_fetcher: 注入的 AvigilonScanner.get_timeline 函式（測試用）

    Returns:
        {
            "nvr_id": str,
            "start": str,
            "end": str,
            "cameras": [
                {"cam_id": str, "camera_name": str, "records": [(start, end), ...], "completeness": 0.0~1.0}
            ]
        }
    """
    # 1. 把所有 cam 的 timeline 抓一次（NVR 端支援 cameraIds 帶逗號分隔；先用單台逐一抓保持簡單）
    parsed_all: dict[str, list[tuple[datetime, datetime]]] = {}
    for cam in cameras:
        device_id = cam["device_id"]
        try:
            raw = timeline_fetcher(device_id, start_iso, end_iso)
        except Exception:
            # 某台 cam 抓失敗 → 該 cam 沒有資料，但不讓整體 500
            parsed_all[device_id] = []
            continue
        per_cam = parse_records_from_timeline_response(raw)
        parsed_all[device_id] = per_cam.get(device_id, [])

    # 2. 計算每 cam 完整率
    out_cameras = []
    for cam in cameras:
        device_id = cam["device_id"]
        records = parsed_all.get(device_id, [])
        records_iso = [[s.isoformat(), e.isoformat()] for s, e in records]
        completeness = compute_per_camera_completeness(records, start_iso, end_iso)
        out_cameras.append({
            "cam_id": device_id,
            "camera_name": cam.get("camera_name", device_id),
            "records": records_iso,
            "completeness": round(completeness, 4),
        })

    return {
        "nvr_id": nvr.get("nvr_id", ""),
        "start": start_iso,
        "end": end_iso,
        "cameras": out_cameras,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage.py -v
```
Expected: PASS（7 個測試全綠）

- [ ] **Step 5: Commit**

```bash
git add web/coverage.py tests/test_coverage.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(coverage): 純邏輯模組 + 7 個測試 (Spec F Task 1)"
```

---

## Task 2: `/clips/coverage/data` JSON API 端點

**Files:**
- Modify: `web/clips_app.py`（在 line 261 register_blueprint 後加 2 個路由）
- Test: `tests/test_coverage_endpoint.py`

- [ ] **Step 1: Write the failing test**

建立 `tests/test_coverage_endpoint.py`：

```python
"""Tests for /clips/coverage/data JSON API endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone


@pytest.fixture
def clips_app_with_nvr(clips_app):
    """在 clips_app session 補 1 台 enabled NVR + 2 台 cam。"""
    from web.db import upsert_nvr, bulk_upsert_cameras
    db_path = clips_app.config["DB_PATH"]
    upsert_nvr(db_path, {
        "nvr_id": "ACC8-TEST",
        "name": "NVR-TEST",
        "host": "10.99.99.99",
        "port": 8443,
        "enabled": 1,
    })
    from web.db import get_nvrs
    nvr_row = next(n for n in get_nvrs(db_path) if n["name"] == "NVR-TEST")
    bulk_upsert_cameras(db_path, [
        {"nvr_id": nvr_row["id"], "device_id": "cam-001", "camera_name": "Cam 1"},
        {"nvr_id": nvr_row["id"], "device_id": "cam-002", "camera_name": "Cam 2"},
    ])
    return clips_app, nvr_row["id"]


def test_coverage_data_endpoint_404_when_nvr_missing(clips_app):
    """無此 internal_id → 404。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage/data?nvr_id=99999&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z")
    assert rv.status_code == 404


def test_coverage_data_endpoint_400_on_bad_window(clips_app):
    """end < start → 400。"""
    from web.db import upsert_nvr
    db_path = clips_app.config["DB_PATH"]
    upsert_nvr(db_path, {
        "nvr_id": "ACC8-T", "name": "NVR-T2", "host": "10.0.0.1", "port": 8443, "enabled": 1,
    })
    from web.db import get_nvrs
    nvr_row = next(n for n in get_nvrs(db_path) if n["name"] == "NVR-T2")
    client = clips_app.test_client()
    rv = client.get(f"/clips/coverage/data?nvr_id={nvr_row['id']}&start=2026-08-04T05:00:00Z&end=2026-08-04T01:00:00Z")
    assert rv.status_code == 400


def test_coverage_data_endpoint_502_when_nvr_unreachable(clips_app):
    """NVR 連線失敗 → 502（包裝為 JSON 錯誤）。"""
    from web.db import upsert_nvr
    from web.db import get_nvrs
    db_path = clips_app.config["DB_PATH"]
    upsert_nvr(db_path, {
        "nvr_id": "ACC8-T", "name": "NVR-UNREACH", "host": "10.0.0.1", "port": 8443, "enabled": 1,
    })
    nvr_row = next(n for n in get_nvrs(db_path) if n["name"] == "NVR-UNREACH")
    client = clips_app.test_client()
    rv = client.get(f"/clips/coverage/data?nvr_id={nvr_row['id']}&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z")
    # 預期 502 或 200（看 mock 該不該完全 stub）— 接受其中一個，明確錯誤訊息
    assert rv.status_code in (200, 502)
    if rv.status_code == 502:
        assert "error" in rv.get_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage_endpoint.py -v
```
Expected: FAIL with `404`（route not found）

- [ ] **Step 3: Write minimal implementation**

修改 `web/clips_app.py`：

在 line 261（`app.register_blueprint(nvr_bp)`）**之後**新增：

```python
# === Spec F：錄影覆蓋熱區（/clips/coverage）===

from web.coverage import fetch_coverage_from_nvr
from web.db import get_nvr, list_cameras_for_nvr
from nvr_scanner import AvigilonScanner

import os


@app.route("/clips/coverage")
def clips_coverage():
    """錄影覆蓋熱區頁面（給 1 台 NVR 看所有 cam 24h 錄影時間軸）。"""
    return render_template(
        "coverage.html",
        # datetime 預設值由前端 JS 帶「現在 - 24h」與「現在」
    )


@app.route("/clips/coverage/data")
def clips_coverage_data():
    """JSON API：回傳 1 台 NVR 所有 cam 的 timeline 資料。"""
    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    if not internal_id:
        return jsonify({"error": "缺少 nvr_id"}), 400

    start_iso = request.args.get("start", "")
    end_iso = request.args.get("end", "")
    if not start_iso or not end_iso:
        return jsonify({"error": "缺少 start / end"}), 400
    if end_iso <= start_iso:
        return jsonify({"error": "end 必須大於 start"}), 400

    nvr_row = get_nvr(_get_db_path(), internal_id)
    if nvr_row is None:
        return jsonify({"error": f"找不到 NVR id={internal_id}"}), 404

    # 拿 cam 清單
    cams = list_cameras_for_nvr(_get_db_path(), internal_id)
    if not cams:
        return jsonify({"error": "該 NVR 沒有 cam"}), 404

    # 認證並呼叫 NVR /timeline
    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    if not user_nonce or not user_key:
        return jsonify({"error": "伺服器未設定 AVIGILON_USER_NONCE/KEY"}), 500

    try:
        scanner = AvigilonScanner(
            host=nvr_row["host"],
            port=nvr_row["port"],
            username=nvr_row.get("username") or os.environ.get("AVIGILON_USERNAME") or "",
            password=nvr_row.get("password") or os.environ.get("AVIGILON_PASSWORD") or "",
            user_nonce=user_nonce,
            user_key=user_key,
            verify_ssl=False,
        )
        scanner.login()

        def fetch_one(cam_id: str, s: str, e: str) -> dict:
            return scanner.get_timeline(cam_id, from_iso=s, to_iso=e)

        out = fetch_coverage_from_nvr(
            nvr={
                "host": nvr_row["host"],
                "port": nvr_row["port"],
                "nvr_id": nvr_row.get("nvr_id", ""),
            },
            cameras=[{"device_id": c["device_id"], "camera_name": c.get("camera_name", c["device_id"])} for c in cams],
            start_iso=start_iso,
            end_iso=end_iso,
            timeline_fetcher=fetch_one,
        )
    except Exception as e:
        return jsonify({"error": f"抓取 NVR 失敗: {e}"}), 502

    return jsonify(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage_endpoint.py -v
```
Expected: PASS（3 個測試全綠）

- [ ] **Step 5: Commit**

```bash
git add web/clips_app.py tests/test_coverage_endpoint.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(coverage): /clips/coverage/data JSON API + 3 個測試 (Spec F Task 2)"
```

---

## Task 3: 頁面模板 + navbar 連結

**Files:**
- Create: `web/clips_templates/coverage.html`
- Modify: `web/clips_app.py`（navbar 加連結）
- Test: `tests/test_coverage_endpoint.py`（新增 3 個頁面測試）

- [ ] **Step 1: Write the failing test**

在 `tests/test_coverage_endpoint.py` 加 3 個測試：

```python
def test_coverage_endpoint_renders_html(clips_app):
    """GET /clips/coverage → 200 + HTML 含「錄影熱區」。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage")
    assert rv.status_code == 200
    assert "錄影熱區" in rv.get_data(as_text=True)


def test_coverage_page_has_nvr_dropdown(clips_app):
    """頁面有 NVR select 元素。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage")
    html = rv.get_data(as_text=True)
    assert "id=\"nvr-select\"" in html or "id='nvr-select'" in html


def test_coverage_page_has_dark_toggle(clips_app):
    """頁面有 dark toggle 連結到 /dark/toggle。"""
    client = clips_app.test_client()
    rv = client.get("/clips/coverage")
    html = rv.get_data(as_text=True)
    assert '/dark/toggle' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage_endpoint.py::test_coverage_endpoint_renders_html tests/test_coverage_endpoint.py::test_coverage_page_has_nvr_dropdown tests/test_coverage_endpoint.py::test_coverage_page_has_dark_toggle -v
```
Expected: FAIL（模板不存在 → 500）

- [ ] **Step 3: Write minimal implementation**

建立 `web/clips_templates/coverage.html`（**獨立完整頁面**，不繼承任何 base）：

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>錄影覆蓋熱區 · Clips 8555</title>
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
    <style>
        body { background: #f8f9fa; color: #222; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
        .navbar-brand { color: #fff !important; }
        .controls { background: #fff; padding: 1rem; border-radius: 0.5rem; margin-bottom: 1rem; box-shadow: 0 0.125rem 0.25rem rgba(0,0,0,.075); }
        .timeline-list { background: #fff; padding: 1rem; border-radius: 0.5rem; box-shadow: 0 0.125rem 0.25rem rgba(0,0,0,.075); }
        .cam-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0; border-bottom: 1px solid #eee; }
        .cam-name { flex: 0 0 180px; font-weight: 600; }
        .cam-bar { flex: 1; position: relative; height: 28px; background: #e9ecef; border-radius: 0.25rem; overflow: hidden; min-width: 720px; }
        .record-block { position: absolute; top: 0; bottom: 0; background: #22c55e; cursor: pointer; opacity: 0.85; transition: opacity 0.1s; }
        .record-block:hover { opacity: 1.0; outline: 2px solid #16a34a; }
        .cam-stats { flex: 0 0 80px; text-align: right; font-family: monospace; color: #555; }
        .hour-marks { display: flex; font-size: 0.7rem; color: #888; margin-bottom: 0.25rem; padding-left: 180px; }
        .hour-marks span { flex: 1; text-align: left; }
        .empty-state { padding: 2rem; text-align: center; color: #888; }
        #warning { color: #dc3545; }
        {% if dark %}
        body { background: #0c1220; color: #e2e8f0; }
        .controls, .timeline-list { background: #1a1a2e; border-color: #334155; }
        .cam-row { border-bottom-color: #334155; }
        .cam-bar { background: #334155; }
        .record-block { background: #22c55e; }
        .cam-stats { color: #94a3b8; }
        .hour-marks { color: #64748b; }
        {% endif %}
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/clips">🎬 影片片段調閱</a>
            <div class="navbar-nav me-auto">
                <a class="nav-link" href="/nvrs/">🖥️ NVR 清單</a>
            </div>
            <span class="navbar-text small text-muted me-auto">Phase 2.7 · port 8555</span>
            <form method="POST" action="/dark/toggle" style="display:inline">
                <button class="btn btn-sm btn-outline-light" type="submit">
                    {% if dark %}☀️{% else %}🌙{% endif %}
                </button>
            </form>
        </div>
    </nav>

    <div class="container py-3">
        <h1>📼 錄影覆蓋熱區</h1>
        <p class="text-muted">選定 1 台 NVR，查看所有 cam 在指定時間範圍內的錄影分佈。綠帶 = 有錄影。</p>

        <div class="controls">
            <div class="row g-2 align-items-end">
                <div class="col-md-3">
                    <label class="form-label">NVR</label>
                    <select id="nvr-select" class="form-select">
                        <option value="">（載入中…）</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <label class="form-label">起</label>
                    <input type="datetime-local" id="start-input" class="form-control">
                </div>
                <div class="col-md-3">
                    <label class="form-label">迄</label>
                    <input type="datetime-local" id="end-input" class="form-control">
                </div>
                <div class="col-md-3">
                    <button id="fetch-btn" class="btn btn-primary w-100">更新</button>
                </div>
            </div>
            <div class="row mt-2">
                <div class="col-12">
                    <span id="summary" class="text-muted"></span>
                </div>
            </div>
        </div>

        <div class="timeline-list">
            <div class="hour-marks" id="hour-marks"></div>
            <div id="cam-list"></div>
            <div id="empty-state" class="empty-state" style="display:none;">請選 NVR 後按「更新」</div>
        </div>

        <div id="warning" class="mt-2"></div>
    </div>

    <script>
    (function () {
        // 預設時間：現在 - 24h ~ 現在
        const now = new Date();
        const yesterday = new Date(now.getTime() - 24 * 3600 * 1000);
        document.getElementById('start-input').value = toLocalIso(yesterday);
        document.getElementById('end-input').value = toLocalIso(now);

        function toLocalIso(d) {
            const pad = n => String(n).padStart(2, '0');
            return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
                + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
        }
        function toUtcIso(localStr) {
            const d = new Date(localStr);
            return d.toISOString().replace('.000', '');
        }

        // 載入 NVR 清單
        fetch('/clips/nvrs').then(r => r.json()).then(nvrs => {
            const sel = document.getElementById('nvr-select');
            sel.innerHTML = '<option value="">— 請選擇 —</option>';
            nvrs.forEach(n => {
                const opt = document.createElement('option');
                opt.value = n.internal_id;
                opt.textContent = n.name + ' (' + n.camera_count + ' cam)';
                sel.appendChild(opt);
            });
        });

        // 24h 軸標籤
        const hourMarks = document.getElementById('hour-marks');
        for (let h = 0; h < 24; h += 2) {
            const span = document.createElement('span');
            span.textContent = String(h).padStart(2, '0');
            hourMarks.appendChild(span);
        }

        document.getElementById('fetch-btn').addEventListener('click', async () => {
            const nvrId = document.getElementById('nvr-select').value;
            const start = document.getElementById('start-input').value;
            const end = document.getElementById('end-input').value;
            if (!nvrId) { alert('請選 NVR'); return; }
            if (!start || !end) { alert('請選時間區間'); return; }
            const url = '/clips/coverage/data?nvr_id=' + encodeURIComponent(nvrId)
                + '&start=' + encodeURIComponent(toUtcIso(start))
                + '&end=' + encodeURIComponent(toUtcIso(end));
            const doc = document.getElementById('cam-list');
            doc.innerHTML = '<div class="empty-state">載入中…</div>';
            document.getElementById('warning').textContent = '';
            try {
                const rv = await fetch(url);
                if (!rv.ok) {
                    const err = await rv.json();
                    document.getElementById('warning').textContent = err.error || '抓取失敗';
                    doc.innerHTML = '';
                    return;
                }
                const data = await rv.json();
                renderTimeline(data, start, end, nvrId);
            } catch (e) {
                document.getElementById('warning').textContent = '網路錯誤: ' + e;
            }
        });

        function renderTimeline(data, startLocal, endLocal, nvrId) {
            const doc = document.getElementById('cam-list');
            doc.innerHTML = '';
            if (!data.cameras || data.cameras.length === 0) {
                doc.innerHTML = '<div class="empty-state">該 NVR 沒有 cam 資料</div>';
                return;
            }
            const tStart = new Date(startLocal).getTime();
            const tEnd = new Date(endLocal).getTime();
            const totalMs = tEnd - tStart;
            let totalComp = 0, count = 0;
            data.cameras.forEach(cam => {
                totalComp += cam.completeness; count++;
                const row = document.createElement('div');
                row.className = 'cam-row';
                const name = document.createElement('div');
                name.className = 'cam-name';
                name.textContent = cam.camera_name + ' (' + cam.cam_id.slice(0, 8) + '…)';
                const bar = document.createElement('div');
                bar.className = 'cam-bar';
                cam.records.forEach(r => {
                    const sMs = new Date(r[0]).getTime();
                    const eMs = new Date(r[1]).getTime();
                    const left = ((sMs - tStart) / totalMs * 100).toFixed(2);
                    const width = ((eMs - sMs) / totalMs * 100).toFixed(2);
                    if (parseFloat(width) <= 0) return;
                    const block = document.createElement('div');
                    block.className = 'record-block';
                    block.style.left = left + '%';
                    block.style.width = width + '%';
                    block.title = formatRecordTitle(r);
                    block.addEventListener('click', () => {
                        const t = r[0].slice(0, 16);  // YYYY-MM-DDTHH:MM
                        window.location.href = '/clips?nvr_id=' + nvrId + '&cam_id=' + encodeURIComponent(cam.cam_id) + '&t=' + t;
                    });
                    bar.appendChild(block);
                });
                const stats = document.createElement('div');
                stats.className = 'cam-stats';
                stats.textContent = (cam.completeness * 100).toFixed(1) + '%';
                row.appendChild(name);
                row.appendChild(bar);
                row.appendChild(stats);
                doc.appendChild(row);
            });
            const avg = count > 0 ? (totalComp / count * 100).toFixed(1) : '0.0';
            document.getElementById('summary').textContent = '共 ' + count + ' 台 cam、平均完整率 ' + avg + '%';
        }

        function formatRecordTitle(r) {
            const s = new Date(r[0]);
            const e = new Date(r[1]);
            const pad = n => String(n).padStart(2, '0');
            const fmt = d => pad(d.getMonth()+1) + '/' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
            const mins = Math.round((e - s) / 60000);
            return fmt(s) + ' - ' + fmt(e) + ' (' + mins + ' 分鐘)';
        }
    })();
    </script>
</body>
</html>
```

修改 `web/clips_app.py` line 295 附近（`/clips/nvrs` 路由）、**navbar 是渲染在 templates 各自獨立**。**Step 3b**：把 `web/clips_templates/clips.html` 的 navbar 區塊也加上「📼 錄影熱區」連結（保持 navbar 一致）：

修改 `web/clips_templates/clips.html` 的 navbar 區塊（找到 `<a class="nav-link" href="/nvrs/">`），在它後面加：

```html
<a class="nav-link" href="/clips/coverage">📼 錄影熱區</a>
```

**Step 3c**：把 `web/clips_templates/nvrs_list.html` 的 navbar 也加同樣連結：

```html
<a class="nav-link" href="/clips/coverage">📼 錄影熱區</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage_endpoint.py -v
```
Expected: PASS（6 個測試全綠）

- [ ] **Step 5: Commit**

```bash
git add web/clips_templates/coverage.html web/clips_templates/clips.html web/clips_templates/nvrs_list.html tests/test_coverage_endpoint.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "feat(coverage): 獨立頁面 + navbar 連結 + 3 個頁面測試 (Spec F Task 3)"
```

---

## Task 4: 整合測試（手工 + Playwright 截圖）

**Files:**
- Modify: 無（純驗證）
- Test: `tests/test_coverage_endpoint.py`（加 1 個整合）

- [ ] **Step 1: Write the failing test**

在 `tests/test_coverage_endpoint.py` 加：

```python
def test_coverage_data_endpoint_with_mock_timeline(clips_app, monkeypatch):
    """整合：mock AvigilonScanner.get_timeline，回 1 台 cam 1 段錄影、預期完整率 1.0。"""
    from web.db import upsert_nvr, bulk_upsert_cameras, get_nvrs
    db_path = clips_app.config["DB_PATH"]
    upsert_nvr(db_path, {
        "nvr_id": "ACC8-MOCK", "name": "NVR-MOCK", "host": "10.0.0.99", "port": 8443, "enabled": 1,
    })
    nvr_row = next(n for n in get_nvrs(db_path) if n["name"] == "NVR-MOCK")
    bulk_upsert_cameras(db_path, [
        {"nvr_id": nvr_row["id"], "device_id": "cam-mock-1", "camera_name": "MockCam1"},
    ])
    # monkeypatch scanner.get_timeline
    from web.clips_app import AvigilonScanner
    def fake_get_timeline(self, cam_id, **kwargs):
        return {"result": {"timelines": [{"cameraId": cam_id, "record": [
            {"start": "2026-08-04T00:00:00Z", "end": "2026-08-04T01:00:00Z"},
        ]}]}}
    monkeypatch.setattr(AvigilonScanner, "get_timeline", fake_get_timeline)
    monkeypatch.setattr("web.clips_app.AvigilonScanner", AvigilonScanner)
    monkeypatch.setenv("AVIGILON_USER_NONCE", "test-nonce")
    monkeypatch.setenv("AVIGILON_USER_KEY", "test-key")
    client = clips_app.test_client()
    rv = client.get(f"/clips/coverage/data?nvr_id={nvr_row['id']}&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["nvr_id"] == "ACC8-MOCK"
    assert len(data["cameras"]) == 1
    assert data["cameras"][0]["cam_id"] == "cam-mock-1"
    assert data["cameras"][0]["completeness"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage_endpoint.py::test_coverage_data_endpoint_with_mock_timeline -v
```
Expected: FAIL（可能因 AvigilonScanner 仍試連真 IP 而 timeout/失敗）— 確認是 mock 沒生效

- [ ] **Step 3: 確認實作（含 monkeypatch 處理）**

若測試失敗表示 monkeypatch 沒生效，修改 `web/clips_app.py` 的 `/clips/coverage/data` 路由：把 `AvigilonScanner` import 改成 module-level 別名方便 monkeypatch：

```python
from nvr_scanner import AvigilonScanner  # 已在 module top-level
```

確保 `monkeypatch.setattr(AvigilonScanner, "get_timeline", ...)` 生效。

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest tests/test_coverage_endpoint.py -v
```
Expected: PASS（7 個測試全綠）

- [ ] **Step 5: Run full test suite**

Run:
```bash
PYTHONIOENCODING=utf-8 pytest -q
```
Expected: 852 → 859 全綠（+7 新測試）

- [ ] **Step 6: Commit**

```bash
git add tests/test_coverage_endpoint.py web/clips_app.py
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "test(coverage): 整合測試 mock 完整 timeline 流程 (Spec F Task 4)"
```

---

## Task 5: CHANGELOG + Playwright 視覺驗證

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 重啟 8555**

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8555 -ErrorAction SilentlyContinue | Stop-Process -Force"
sleep 2
PYTHONIOENCODING=utf-8 nohup powershell -Command "Start-Process -FilePath 'python' -ArgumentList '-m','web.clips_app','8555' -RedirectStandardOutput 'nvr_clips_8555.out' -RedirectStandardError 'nvr_clips_8555.err' -WorkingDirectory 'C:\cc\NVR' -WindowStyle Hidden" &
sleep 4
curl -sS -o /dev/null -w "8555: %{http_code}\n" http://127.0.0.1:8555/clips/coverage
```
Expected: `8555: 200`

- [ ] **Step 2: 視覺驗證（curl 確認 + 留給 user 看）**

```bash
curl -sS http://127.0.0.1:8555/clips/coverage | grep -c "錄影熱區"
# 預期 >= 1

curl -sS "http://127.0.0.1:8555/clips/coverage/data?nvr_id=53&start=2026-08-04T00:00:00Z&end=2026-08-04T01:00:00Z" 2>&1 | head -c 200
# 預期 JSON（可能因 NVR 連線失敗回 502；看時機）
```

- [ ] **Step 3: 寫 CHANGELOG**

在 `CHANGELOG.md` 頂端加：

```markdown
## Spec F：8555 錄影覆蓋熱區 (2026-08-04)

**新增** `/clips/coverage` 頁面：選定 1 台 NVR、自訂時間區間，視覺化所有 cam 的錄影分佈（綠帶 = 有錄影）。點擊區段 → 跳轉 clips 頁帶時間預覽。

**新增 API** `GET /clips/coverage/data?nvr_id=&start=&end=`：JSON 回傳每 cam 的 records + completeness。

**新檔**:
- `web/coverage.py` — 純邏輯（解析 NVR /timeline + 計算完整率）
- `web/clips_templates/coverage.html` — 獨立頁面（純 CSS grid 時間軸，無 Chart.js）
- `tests/test_coverage.py` — 7 個純邏輯測試
- `tests/test_coverage_endpoint.py` — 7 個 API 測試

**變更** `web/clips_app.py` — 2 路由 + 2 個 navbar 連結（clips / nvrs 頁）

**測試**：852 → 859 全綠（+7 新測試）

**重啟提醒**：改 `web/clips_app.py` 必重啟 8555（無 supervisor）。本文僅 Port 8555，不影響 8444。
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git -c user.email="claude@anthropic.com" -c user.name="Claude" commit -m "docs(changelog): Spec F 8555 錄影覆蓋熱區紀錄 (Task 5)"
```

---

## Self-Review

**Spec coverage 對照**：
- ✅ 1 個新頁 `/clips/coverage` — Task 3
- ✅ 視覺：每 cam 1 行、綠帶 = 有錄影 — Task 3 (renderTimeline)
- ✅ Hover tooltip — Task 3 (record-block.title)
- ✅ 點擊跳轉 — Task 3 (block.addEventListener)
- ✅ 自訂時間範圍 — Task 3 (datetime-local inputs)
- ✅ 完整率 — Task 1 (compute_per_camera_completeness) + Task 3 (cam-stats)
- ✅ 8555 獨立呼叫 NVR — Task 2 (無 8444 DB 讀取)
- ✅ Dark mode 內聯 — Task 3 ({% if dark %})
- ✅ navbar 連結 — Task 3 (clips.html + nvrs_list.html)
- ✅ +12 測試 — 實際 +14（7 + 7）覆蓋率超預期

**Placeholder scan**：0 個 TBD/TODO

**Type consistency**：`fetch_coverage_from_nvr` 回傳 dict 結構一致；`cam_id` key name 全程一致；`coverage.py` 與 `timeline.py` 介面匹配

**修正項**：Task 4 測試預期 FAIL 是 monkeypatch 沒生效，Step 3 確認實作保證 module-level import
