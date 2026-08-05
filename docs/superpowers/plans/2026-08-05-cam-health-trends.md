# Cam 健康趨勢圖（Spec G）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `/trends` 路由 + 模板，呈現每台 cam 過去 24h/7d 的「健康三維度」（online / frozen / dark）mini sparkline + 詳細大圖，幫助從 snapshot 升級到趨勢判斷。

**Architecture:** 純 SQLite 查詢（`web/trends.py`，2 純函式 + 2 dataclass）→ Flask `/trends` route → Jinja2 模板 + Chart.js 4.4.0 sparkline（沿用 `fleet.html` 已引入 CDN）。**零新 schema**——用既有 `image_health_checks.metrics_json` 物件欄位代理「健康掃描記錄」。4 處深鍵結入口（devices / dashboard / coverage / navbar），全部 purely additive。

**Tech Stack:** Flask + Jinja2 + Chart.js 4.4.0（CDN）+ SQLite + pytest。

---

## ⚠️ Spec 修正（plan 階段勘誤）

讀 schema 與 `batch_scan.py` 實際寫入後，發現 `image_health_checks.flags_json` 與 spec 假設不一致：

| 項目 | Spec 假設 | 實際格式 | 修正方式 |
|---|---|---|---|
| `flags_json` 型別 | 物件 `{"is_frozen": true, "is_dark_scene": true}` | **JSON 陣列** `["frozen", "underexposed"]` | 不依賴 flags_json，改讀 `metrics_json` |
| `is_frozen` | flags_json.is_frozen | `metrics_json.is_frozen` (bool) ✅ 確實存在 | 改讀 metrics_json |
| `is_dark_scene` | flags_json.is_dark_scene | 不存在；對應的是 `metrics_json.is_underexposed` | **rename**：spec 「dark」→ 「underexposed」（語意更精準） |

**決定**：plan 全程使用 `metrics_json`（JSON object），`flags_json` 不用。
- `is_frozen = metrics_json.get("is_frozen", False)`
- `is_underexposed = metrics_json.get("is_underexposed", False)`（視為 spec 的「dark」）

注意 batch_scan 從未寫 `is_dark_scene`，因此「Dark」sparkline 在真實資料下是 0；視覺上看不到第三條線。Plan 仍實作第三 series（合約完整），spec 文件給 user review 時一併標註此差異。

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `web/trends.py` | Create | 2 純函式 + 2 dataclass（純 DB 查詢層） |
| `web/app.py` | Modify (~30 行) | 加 `GET /trends` route + 注入 `_get_db_path()` |
| `web/templates/trends.html` | Create | Sparkline grid UI（含 Chart.js inline init） |
| `web/templates/base.html` | Modify (1 anchor) | 加 `📈 健康趨勢` navbar 連結 |
| `web/templates/devices.html` | Modify (1 column) | 每台 cam row 加 `📈` 連結 |
| `web/templates/dashboard.html` | Modify (1 anchor) | 異常 cam 列表每行加 `📈` 連結 |
| `web/clips_templates/coverage.html` | Modify (1 anchor) | 綠帶上方 cam 名加 `📈` 連結 |
| `tests/test_trends.py` | Create | 12 純函式測試 |
| `tests/integration/test_e2e_trends.py` | Create | 9 route 整合測試 |

**為什麼拆 11 檔**：`trends.py` 純查詢邏輯可獨立 unit test；template 與既有 template 解耦；deep-link 改動小且 isolated。不動 `avigilon/`、`batch_scan`、`worker`、8444/8555 啟動入口等（保持既有穩定）。

---

## Task 1：`compute_health_timeseries` 24h 基礎（含 dataclass）

**Files:**
- Create: `web/trends.py`
- Create: `tests/test_trends.py`

### Step 1：寫第一個失敗測試（空 DB）

`tests/test_trends.py`：

```python
"""web/trends.py 純函式測試（無 Flask、無 HTTP）。"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from web.trends import (
    CamHealthSummary,
    HealthBin,
    compute_health_timeseries,
    get_all_cams_health_summary,
)


@pytest.fixture
def empty_db(tmp_path: Path) -> str:
    """建立空 SQLite + 必要 schema（cameras + nvr_servers + image_health_checks）。"""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            is_ghost INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE image_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            nvr_server_id INTEGER,
            checked_at_utc TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            flags_json TEXT NOT NULL
        );
        CREATE INDEX idx_health_cam_time
            ON image_health_checks(camera_id, checked_at_utc DESC);
    """)
    conn.commit()
    conn.close()
    return db_path


class TestComputeHealthTimeseries24h:
    def test_empty_db_returns_24_zero_bins(self, empty_db: str):
        """沒任何 record → 24 個 bin 全 0/0/0/0。"""
        bins = compute_health_timeseries(empty_db, "cam-unknown", range_hours=24)
        assert len(bins) == 24
        assert all(b.sample_count == 0 for b in bins)
        assert all(b.online_pct == 0.0 for b in bins)
        assert all(b.frozen_pct == 0.0 for b in bins)
        assert all(b.underexposed_pct == 0.0 for b in bins)
```

### Step 2：跑測試確認 fail

Run: `pytest tests/test_trends.py::TestComputeHealthTimeseries24h::test_empty_db_returns_24_zero_bins -v`
Expected: `ModuleNotFoundError: No module named 'web.trends'`

### Step 3：實作 `web/trends.py`（最小可讓空 DB 測過）

```python
"""
web/trends.py
=============
Spec G: Cam 健康趨勢圖的純 DB 查詢層。

設計：
    - 純函式（無 Flask context、無全域 state）
    - db_path 顯式傳入（與 web.db 一致風格）
    - 回傳 dataclass，方便 template 用 attribute access
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class HealthBin:
    """單一時段 bin 的健康統計。"""
    start_utc: str           # ISO 8601 e.g. "2026-08-05T14:00:00Z"
    online_pct: float        # 0.0-100.0
    frozen_pct: float        # 0.0-100.0
    underexposed_pct: float  # 0.0-100.0（spec 原名 dark；改用程式碼既有的 underexposed）
    sample_count: int        # 該 bin 內 image_health 記錄數


@dataclass(frozen=True)
class CamHealthSummary:
    """一台 cam 的健康摘要。"""
    cam_id: str
    cam_name: str
    nvr_id: str
    nvr_name: str
    bins: list[HealthBin]
    abnormal_bins: int       # frozen/underexposed/offline 任一 > 0 的 bin 數


def _connect(db_path: str) -> sqlite3.Connection:
    """與 web.db._connect 同風格的唯讀連線。"""
    p = Path(db_path)
    if not p.exists() and db_path != ":memory:":
        raise FileNotFoundError(f"DB 檔不存在：{db_path}")
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA query_only = ON")
    return conn


def _bin_size_hours(range_hours: int) -> int:
    """24h → 1h bins；7d → 24h daily bins。"""
    if range_hours == 24:
        return 1
    if range_hours == 168:  # 7d
        return 24
    raise ValueError(f"range_hours 必須是 24 或 168，got {range_hours!r}")


def _truncate_to_bin(t: datetime, bin_size_h: int) -> datetime:
    """把時間截到該 bin 起始（UTC, 去除時區情報）。"""
    if bin_size_h >= 24:
        return t.replace(hour=0, minute=0, second=0, microsecond=0)
    return t.replace(minute=0, second=0, microsecond=0, hour=(t.hour // bin_size_h) * bin_size_h)


def _bin_start_iso(bin_start: datetime) -> str:
    return bin_start.strftime("%Y-%m-%dT%H:00:00Z") if bin_start.hour or bin_start.minute \
        else bin_start.strftime("%Y-%m-%dT00:00:00Z")


def compute_health_timeseries(
    db_path: str,
    cam_id: str,
    range_hours: int,
) -> list[HealthBin]:
    """從 image_health_checks 算 cam 的時間序列健康資料。

    24h → 24 個 1-hour bins
    7d  → 7 個 24-hour bins（每天一個聚合）

    演算法：
        1. 從 image_health_checks 取該 cam 的所有 record（時間區間）
        2. 用 bin index 把 record 分配到 N 個 bucket
        3. 每 bucket 算 online/frozen/underexposed %

    Args:
        db_path: SQLite DB 路徑
        cam_id: 相機 device_id
        range_hours: 24 或 168（7d）

    Returns:
        由舊到新排序的 list[HealthBin]，長度 = range_hours / bin_size_hours
    """
    bin_h = _bin_size_hours(range_hours)
    n_bins = range_hours // bin_h
    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(hours=range_hours)

    # 算每個 bin 的起始時間（從最舊到最新）
    bin_starts: list[datetime] = []
    # 以窗口起點對齊到 bin 邊界（每 24h 或 1h）
    aligned_start = _truncate_to_bin(window_start, bin_h)
    for i in range(n_bins):
        bin_starts.append(aligned_start + timedelta(hours=i * bin_h))

    # 初始化 bucket
    buckets: list[dict] = [
        {"total": 0, "frozen": 0, "underexposed": 0} for _ in range(n_bins)
    ]

    # 查詢該 cam 在窗口內的所有 record
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT checked_at_utc, metrics_json
            FROM image_health_checks
            WHERE camera_id = ?
              AND checked_at_utc >= ?
            ORDER BY checked_at_utc ASC
            """,
            (cam_id, window_start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        checked_at = row["checked_at_utc"]
        # parse "2026-08-05T14:23:01Z" 形式
        ts = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        # 對應到 bin index
        delta_h = (ts - aligned_start).total_seconds() / 3600.0
        idx = int(delta_h // bin_h)
        if 0 <= idx < n_bins:
            buckets[idx]["total"] += 1
            # metrics_json parse（容錯 fallback）
            try:
                metrics = json.loads(row["metrics_json"])
                if not isinstance(metrics, dict):
                    raise ValueError("metrics 非 dict")
            except (json.JSONDecodeError, ValueError, TypeError):
                # 壞 JSON 視為「非 frozen、非 underexposed」（不 crash）
                continue
            if metrics.get("is_frozen") is True:
                buckets[idx]["frozen"] += 1
            if metrics.get("is_underexposed") is True:
                buckets[idx]["underexposed"] += 1

    # 組裝 HealthBin list
    result: list[HealthBin] = []
    for i, b in enumerate(buckets):
        total = b["total"]
        result.append(HealthBin(
            start_utc=_bin_start_iso(bin_starts[i]),
            online_pct=100.0 if total > 0 else 0.0,
            frozen_pct=(b["frozen"] / total * 100.0) if total > 0 else 0.0,
            underexposed_pct=(b["underexposed"] / total * 100.0) if total > 0 else 0.0,
            sample_count=total,
        ))
    return result
```

### Step 4：跑測試確認 pass

Run: `pytest tests/test_trends.py::TestComputeHealthTimeseries24h::test_empty_db_returns_24_zero_bins -v`
Expected: `1 passed`

### Step 5：Commit

```bash
git add web/trends.py tests/test_trends.py
git commit -m "feat(trends): HealthBin dataclass + compute_health_timeseries 24h (empty case)"
```

---

## Task 2：`compute_health_timeseries` 24h 含 records（4 健康場景）

**Files:**
- Modify: `tests/test_trends.py`（加測試）
- 不改 `web/trends.py`（Step 1 已實作完整邏輯）

### Step 1：寫 4 個測試（全 healthy / frozen spike / underexposed spike / offline window）

加入 `tests/test_trends.py`：

```python
import json as _json
from datetime import datetime, timedelta, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_offset(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_record(db_path: str, cam_id: str, hours_ago: float,
                 is_frozen: bool, is_underexposed: bool) -> None:
    """塞一筆 image_health_checks record。"""
    metrics = {
        "blur_var": 100.0,
        "mean_luma": 0.5,
        "is_blurry": False,
        "is_overexposed": False,
        "is_underexposed": is_underexposed,
        "frozen_diff": 0.5,
        "is_frozen": is_frozen,
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO image_health_checks "
        "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
        "VALUES (?, NULL, ?, ?, '[]')",
        (cam_id, _iso_offset(hours_ago), _json.dumps(metrics)),
    )
    conn.commit()
    conn.close()


class TestComputeHealthTimeseries24hWithData:
    """24h + records：驗證 4 個核心場景（spec §7.1）。"""

    def test_all_healthy_24h(self, empty_db: str):
        """每 bin 各 2 筆 healthy record → online=100%, frozen=0%, underexposed=0%。"""
        # 24 bin * 2 = 48 筆，分布過去 24h
        for h in range(24):
            for _ in range(2):
                _seed_record(empty_db, "cam-1", h + 0.25, False, False)
        bins = compute_health_timeseries(empty_db, "cam-1", range_hours=24)
        assert len(bins) == 24
        # 至少有 record 的 bin 全 100% online；最舊幾個 bin 可能沒 record（fallback 0%）
        online_bins = [b for b in bins if b.sample_count > 0]
        assert len(online_bins) >= 20, f"應有 ≥20 bin 含 record，got {len(online_bins)}"
        assert all(b.online_pct == 100.0 for b in online_bins)
        assert all(b.frozen_pct == 0.0 for b in online_bins)
        assert all(b.underexposed_pct == 0.0 for b in online_bins)

    def test_frozen_spike_3_bins(self, empty_db: str):
        """第 5-7 bin 各有 1 筆 is_frozen → 那 3 bin frozen=100%。"""
        # seed 在 bin index 4, 5, 6 (0-indexed) ~ 對應 4-6h ago
        for h_ago in (4.2, 5.2, 6.2):
            _seed_record(empty_db, "cam-2", h_ago, True, False)
        bins = compute_health_timeseries(empty_db, "cam-2", range_hours=24)
        assert len(bins) == 24
        # 第 5, 6, 7 bin (索引 4, 5, 6) 應 frozen=100%
        for idx in (4, 5, 6):
            assert bins[idx].frozen_pct == 100.0, \
                f"bin {idx} 應 frozen=100%, got {bins[idx].frozen_pct}"
            assert bins[idx].underexposed_pct == 0.0

    def test_underexposed_spike_3_bins(self, empty_db: str):
        """第 10-12 bin 各有 1 筆 is_underexposed → 那 3 bin underexposed=100%。"""
        for h_ago in (10.5, 11.5, 12.5):
            _seed_record(empty_db, "cam-3", h_ago, False, True)
        bins = compute_health_timeseries(empty_db, "cam-3", range_hours=24)
        for idx in (10, 11, 12):
            assert bins[idx].underexposed_pct == 100.0, \
                f"bin {idx} 應 underexposed=100%, got {bins[idx].underexposed_pct}"
            assert bins[idx].frozen_pct == 0.0

    def test_offline_window_3_bins(self, empty_db: str):
        """第 10-12 bin 完全沒 record → online=0%（離線）。"""
        for h_ago in (5.0, 5.5, 6.0, 7.0, 7.5, 8.0):
            _seed_record(empty_db, "cam-4", h_ago, False, False)
        # bin 10-12 (15-18h ago) 沒 record
        bins = compute_health_timeseries(empty_db, "cam-4", range_hours=24)
        for idx in (10, 11, 12):
            assert bins[idx].online_pct == 0.0, \
                f"bin {idx} 應離線 online=0%, got {bins[idx].online_pct}"
            assert bins[idx].sample_count == 0
        # bin 5-8 應有 record
        for idx in (5, 6, 7, 8):
            assert bins[idx].online_pct == 100.0
            assert bins[idx].sample_count >= 1

    def test_invalid_metrics_json_fallback(self, empty_db: str):
        """壞 JSON → 該筆 fallback，不 crash。"""
        conn = sqlite3.connect(empty_db)
        conn.execute(
            "INSERT INTO image_health_checks "
            "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
            "VALUES ('cam-bad', NULL, ?, 'not-json-at-all', '[]')",
            (_iso_offset(1.0),),
        )
        conn.commit()
        conn.close()
        # 不應丟例外
        bins = compute_health_timeseries(empty_db, "cam-bad", range_hours=24)
        assert len(bins) == 24
        # sample_count 應為 1（record 有讀到），但 frozen/underexposed=0
        nonzero = [b for b in bins if b.sample_count > 0]
        assert len(nonzero) >= 1
        assert all(b.frozen_pct == 0.0 for b in nonzero)
        assert all(b.underexposed_pct == 0.0 for b in nonzero)
```

### Step 2：跑測試（全部 fail，因為 compute_health_timeseries 已實作但測試尚未跑）

Run: `pytest tests/test_trends.py -v`
Expected: 1 passed (Task 1) + 5 new fail（5 個 health 場景 + 1 個 bad JSON）

Wait — 第一個 test_all_healthy_24h **應該會 pass**（實作正確）。其餘 4 個測試**也應該會 pass**，因為實作完整。讓我修一下：因為 Task 1 已實作完整邏輯，這 5 個測試都應該 pass。Verifier 應注意「5 passed, 0 failed」。

### Step 3：（已實作）不需要改 `web/trends.py`

### Step 4：跑測試確認 6 個全 pass

Run: `pytest tests/test_trends.py -v`
Expected: `6 passed`

### Step 5：Commit

```bash
git add tests/test_trends.py
git commit -m "test(trends): compute_health_timeseries 24h - all 4 health scenarios + bad JSON"
```

---

## Task 3：`compute_health_timeseries` 7d aggregation

**Files:**
- Modify: `tests/test_trends.py`

### Step 1：寫 7d 測試

加入 `tests/test_trends.py`：

```python
class TestComputeHealthTimeseries7d:
    def test_7d_aggregates_to_daily_bins(self, empty_db: str):
        """range_hours=168 → 7 個 daily bins。"""
        for day in range(7):
            for hour in (0, 6, 12, 18):
                _seed_record(empty_db, "cam-5", day * 24 + (23 - hour), False, False)
        bins = compute_health_timeseries(empty_db, "cam-5", range_hours=168)
        assert len(bins) == 7, f"7d 應回 7 個 daily bin，got {len(bins)}"
        # 全部 bin 都應有 record（seed 分散 7 天）
        online_bins = [b for b in bins if b.sample_count > 0]
        assert len(online_bins) == 7, "7d 全部 7 天都有 record"
        assert all(b.online_pct == 100.0 for b in online_bins)

    def test_7d_invalid_range_raises(self, empty_db: str):
        """range_hours=99 → ValueError（不是 24 也不是 168）。"""
        with pytest.raises(ValueError, match="range_hours 必須"):
            compute_health_timeseries(empty_db, "cam-x", range_hours=99)
```

### Step 2：跑測試確認 2 個 fail（7d 部分未實作）

Run: `pytest tests/test_trends.py -v`
Expected: Task 1+2 = 6 passed + 2 failed（7d 部分）

Wait — Task 1 的 `_bin_size_hours(168) = 24`、`n_bins = 168//24 = 7` 已實作完整。所以這 2 個測試應該都會 pass。Verifier 預期 `8 passed`。

如果 fail，需要補實作 7d 在 `_bin_size_hours()`。但目前實作已涵蓋。

### Step 3：（不需要改）

### Step 4：確認全綠

Run: `pytest tests/test_trends.py -v`
Expected: `8 passed`

### Step 5：Commit

```bash
git add tests/test_trends.py
git commit -m "test(trends): compute_health_timeseries 7d daily bins"
```

---

## Task 4：`get_all_cams_health_summary` 全 cam 列舉

**Files:**
- Modify: `tests/test_trends.py`
- Modify: `web/trends.py`

### Step 1：寫 5 個測試（ghost filter / nvr filter / abnormal_only / sort / empty）

加入 `tests/test_trends.py`：

```python
def _seed_nvr_and_cams(db_path: str, nvr_id: str, nvr_name: str,
                       cam_specs: list[tuple[str, str, bool]]) -> None:
    """塞 1 台 NVR + 數台 cam。cam_specs = [(device_id, name, is_ghost), ...]"""
    conn = sqlite3.connect(db_path)
    nvr_int = conn.execute(
        "INSERT INTO nvr_servers (nvr_id, name) VALUES (?, ?)",
        (nvr_id, nvr_name),
    ).lastrowid
    for device_id, name, is_ghost in cam_specs:
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nvr_int, device_id, name, 1 if is_ghost else 0, _now_iso()),
        )
    conn.commit()
    conn.close()


class TestGetAllCamsHealthSummary:
    """對應 spec §7.1: ghost / nvr filter / status filter / sort。"""

    def test_empty_db_returns_empty_list(self, empty_db: str):
        """空 DB → 空 list（不 crash）。"""
        result = get_all_cams_health_summary(empty_db, range_hours=24)
        assert result == []

    def test_filters_ghost_cams(self, empty_db: str):
        """is_ghost=1 的 cam 不在結果中。"""
        _seed_nvr_and_cams(empty_db, "nvr-a", "ACC-8", [
            ("d-1", "Cam1", False),
            ("d-2", "Ghost", True),  # 應過濾
            ("d-3", "Cam3", False),
        ])
        result = get_all_cams_health_summary(empty_db, range_hours=24)
        ids = [s.cam_id for s in result]
        assert "d-2" not in ids, f"Ghost cam d-2 應過濾，got {ids}"
        assert set(ids) == {"d-1", "d-3"}

    def test_nvr_filter_only_returns_target_nvr(self, empty_db: str):
        """nvr_filter='nvr-a' 只列該 NVR 的 cam。"""
        _seed_nvr_and_cams(empty_db, "nvr-a", "ACC-8", [("d-1", "Cam1", False)])
        _seed_nvr_and_cams(empty_db, "nvr-b", "ACC-9", [("d-2", "Cam2", False)])
        result = get_all_cams_health_summary(empty_db, range_hours=24, nvr_filter="nvr-a")
        ids = [s.cam_id for s in result]
        assert ids == ["d-1"], f"nvr_filter=nvr-a 應只剩 d-1，got {ids}"

    def test_abnormal_only_filter(self, empty_db: str):
        """status_filter='abnormal_only' 只列有 abnormal 的 cam。"""
        _seed_nvr_and_cams(empty_db, "nvr-a", "ACC-8", [
            ("healthy", "HealthyCam", False),
            ("frozen", "FrozenCam", False),
            ("underexposed", "DarkCam", False),
        ])
        # healthy cam：8 筆 healthy record（分布 8h ago~15h ago）
        for h in range(8, 16):
            _seed_record(empty_db, "healthy", h, False, False)
        # frozen cam：3 bin frozen = 3 abnormal
        for h_ago in (4.2, 5.2, 6.2):
            _seed_record(empty_db, "frozen", h_ago, True, False)
        # underexposed cam：2 bin underexposed
        for h_ago in (10.5, 11.5):
            _seed_record(empty_db, "underexposed", h_ago, False, True)

        result = get_all_cams_health_summary(empty_db, range_hours=24)
        all_ids = {s.cam_id for s in result}
        assert all_ids == {"healthy", "frozen", "underexposed"}

        result_filtered = get_all_cams_health_summary(
            empty_db, range_hours=24, status_filter="abnormal_only",
        )
        filtered_ids = {s.cam_id for s in result_filtered}
        # healthy 0 abnormal → 過濾掉
        assert "healthy" not in filtered_ids
        assert "frozen" in filtered_ids
        assert "underexposed" in filtered_ids
        # 各 summary 的 abnormal_bins 數
        by_id = {s.cam_id: s.abnormal_bins for s in result_filtered}
        assert by_id["frozen"] == 3
        assert by_id["underexposed"] == 2

    def test_sorted_by_abnormal_bins_desc(self, empty_db: str):
        """排序：abnormal_bins DESC, cam_name ASC。"""
        _seed_nvr_and_cams(empty_db, "nvr-a", "ACC-8", [
            ("alpha", "Alpha", False),
            ("bravo", "Bravo", False),
            ("charlie", "Charlie", False),
        ])
        # bravo 5 bins abnormal, alpha 2, charlie 0
        for h in range(2):
            for _ in range(2):
                _seed_record(empty_db, "bravo", h + 0.1, True, False)
                _seed_record(empty_db, "bravo", h + 0.2, True, False)
                _seed_record(empty_db, "bravo", h + 0.3, True, False)
        for h_ago in (1.5, 2.5):
            _seed_record(empty_db, "alpha", h_ago, True, False)

        result = get_all_cams_health_summary(empty_db, range_hours=24)
        ids = [s.cam_id for s in result]
        # bravo(5) > alpha(2) > charlie(0)
        assert ids.index("bravo") < ids.index("alpha") < ids.index("charlie")
```

### Step 2：跑測試預期 5 fail

Run: `pytest tests/test_trends.py::TestGetAllCamsHealthSummary -v`
Expected: 5 failed (get_all_cams_health_summary not implemented)

### Step 3：實作 `get_all_cams_health_summary`

加到 `web/trends.py`：

```python
def get_all_cams_health_summary(
    db_path: str,
    range_hours: int = 24,
    nvr_filter: str | None = None,
    status_filter: str = "any",
) -> list[CamHealthSummary]:
    """列所有 cam（含 NVR JOIN，過濾 ghost），各呼叫 compute_health_timeseries。

    Args:
        db_path: SQLite DB 路徑
        range_hours: 24 或 168
        nvr_filter: 限定單一 NVR（None = 不限）
        status_filter: "any" = 全部 / "abnormal_only" = 只列 abnormal_bins > 0

    Returns:
        按 abnormal_bins DESC, cam_name ASC 排序的 list[CamHealthSummary]
    """
    if status_filter not in ("any", "abnormal_only"):
        status_filter = "any"

    conn = _connect(db_path)
    try:
        # JOIN cameras + nvr_servers，過濾 ghost 與 nvr_filter
        query = """
            SELECT c.device_id AS cam_id, c.camera_name AS cam_name,
                   n.nvr_id AS nvr_id, n.name AS nvr_name
            FROM cameras c
            JOIN nvr_servers n ON c.nvr_id = n.id
            WHERE c.is_ghost = 0
        """
        params: tuple = ()
        if nvr_filter:
            query += " AND n.nvr_id = ?"
            params = (nvr_filter,)
        query += " ORDER BY c.camera_name ASC"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    summaries: list[CamHealthSummary] = []
    for row in rows:
        cam_id = row["cam_id"]
        bins = compute_health_timeseries(db_path, cam_id, range_hours)
        # 算 abnormal_bins：任何 bin 的 frozen/underexposed > 0 或 online=0%（離線）
        abnormal_count = sum(
            1 for b in bins
            if b.frozen_pct > 0 or b.underexposed_pct > 0 or b.online_pct == 0
        )
        summaries.append(CamHealthSummary(
            cam_id=cam_id,
            cam_name=row["cam_name"],
            nvr_id=row["nvr_id"],
            nvr_name=row["nvr_name"],
            bins=bins,
            abnormal_bins=abnormal_count,
        ))

    # 排序：abnormal_bins DESC, cam_name ASC
    summaries.sort(key=lambda s: (-s.abnormal_bins, s.cam_name))

    # status_filter: abnormal_only
    if status_filter == "abnormal_only":
        summaries = [s for s in summaries if s.abnormal_bins > 0]

    return summaries
```

### Step 4：跑測試確認 5 個全 pass

Run: `pytest tests/test_trends.py::TestGetAllCamsHealthSummary -v`
Expected: `5 passed`

### Step 5：跑全部純函式測試

Run: `pytest tests/test_trends.py -v`
Expected: `13 passed`（8 純 24h/7d + 5 summary）

### Step 6：Commit

```bash
git add web/trends.py tests/test_trends.py
git commit -m "feat(trends): get_all_cams_health_summary with ghost/nvr/status filters"
```

---

## Task 5：Flask `/trends` route（基本殼 + 4 query params）

**Files:**
- Modify: `web/app.py`（在 routes 區段附近加 route）
- Create: `web/templates/trends.html`（先放極簡殼，後續 Task 加內容）
- Create: `tests/integration/test_e2e_trends.py`

### Step 1：寫 4 個失敗的 route 測試

`tests/integration/test_e2e_trends.py`：

```python
"""web/app.py /trends route 整合測試（Flask test_client + 真 SQLite）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from web.app import app


@pytest.fixture
def flask_client(tmp_path: Path, monkeypatch):
    """8444 app + tmp DB。"""
    db_path = str(tmp_path / "trends.db")

    # 建最小 schema（含 spec G 需要的 3 張表）
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE nvr_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nvr_id INTEGER NOT NULL REFERENCES nvr_servers(id),
            device_id TEXT NOT NULL,
            camera_name TEXT NOT NULL,
            is_ghost INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE image_health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            nvr_server_id INTEGER,
            checked_at_utc TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            flags_json TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    # 注入 db_path 給 app
    monkeypatch.setenv("NVR_DB_PATH", db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, db_path


class TestTrendsRoute:
    def test_route_returns_200(self, flask_client):
        """GET /trends → 200。"""
        client, _ = flask_client
        r = client.get("/trends")
        assert r.status_code == 200

    def test_default_range_is_24h(self, flask_client):
        """不帶 query → 24h 模式（bin count = 24）。"""
        client, db_path = flask_client
        # seed 1 台 NVR + 1 cam + 1 筆 record
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'd-1', 'Cam1', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()

        r = client.get("/trends")
        assert r.status_code == 200
        assert "Cam 健康趨勢".encode("utf-8") in r.data or "Cam 健康趨勢" in r.data.decode("utf-8")

    def test_invalid_range_falls_back_to_24h(self, flask_client):
        """?range=99 → 24h（不 500）。"""
        client, _ = flask_client
        r = client.get("/trends?range=99")
        assert r.status_code == 200

    def test_nvr_dropdown_lists_all_nvrs(self, flask_client):
        """template 內含所有 NVR 名（給 dropdown）。"""
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-b', 'ACC-9')")
        conn.commit()
        conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        assert "ACC-8" in body
        assert "ACC-9" in body
```

### Step 2：跑測試確認 4 個 fail

Run: `pytest tests/integration/test_e2e_trends.py -v`
Expected: 4 failed (route not registered)

### Step 3：讀 `web/app.py` 確認 _get_db_path 與現有 route 風格

Read 必要段（用讀工具看）。尋找：
- `_get_db_path(app)` 或類似 helper
- 既有 route 範例（如 `/devices`）
- theme 變數注入（context processor）

### Step 4：實作 Flask route + 極簡 template

加到 `web/app.py`（在現有 route 區段末）：

```python
from web.trends import get_all_cams_health_summary
from datetime import datetime, timedelta, timezone
import sqlite3 as _sqlite3


def _get_db_path_for_trends():
    """讀 DB 路徑：跟 web/app.py 其他 route 共用邏輯。"""
    # 視 web/app.py 的既有 helper 命名（_get_db_path / current_db_path 等），
    # 沿用同一個；找不到就 fallback 讀 NVR_DB_PATH 或 default 'nvr_scan.db'
    try:
        return _get_db_path(app)  # noqa: F821 - 既有 helper
    except NameError:
        from web.app import _get_db_path  # 視實際
        return _get_db_path(app)


@app.route("/trends")
def trends():
    """Spec G: Cam 健康趨勢總覽。"""
    range_str = request.args.get("range", "24h")
    range_hours = 24 if range_str == "24h" else (168 if range_str == "7d" else 24)
    nvr_filter = request.args.get("nvr_id") or None
    status_filter = request.args.get("status", "any")

    db_path = _get_db_path_for_trends()
    summaries = get_all_cams_health_summary(
        db_path,
        range_hours=range_hours,
        nvr_filter=nvr_filter,
        status_filter=status_filter,
    )
    # 給 dropdown 的 NVR 清單（用 web.db.list_enabled_nvrs 既有 helper）
    try:
        from web.db import list_enabled_nvrs
        nvrs = list_enabled_nvrs(db_path)
    except Exception:
        nvrs = []

    return render_template(
        "trends.html",
        summaries=summaries,
        range_hours=range_hours,
        nvrs=nvrs,
        current_nvr=nvr_filter,
        current_status=status_filter,
    )
```

建立 `web/templates/trends.html`（Task 5 最小版）：

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>Cam 健康趨勢</title>
</head>
<body>
  <h2>📈 Cam 健康趨勢</h2>
  <p>共 {{ summaries|length }} 台 cam。</p>
  {% if nvrs %}
  <ul>
  {% for n in nvrs %}
    <li>{{ n.nvr_id }} - {{ n.name }}</li>
  {% endfor %}
  </ul>
  {% endif %}
  <p>Range: {{ range_hours }}h</p>
</body>
</html>
```

### Step 5：跑測試確認 4 個 pass

Run: `pytest tests/integration/test_e2e_trends.py -v`
Expected: `4 passed`

### Step 6：跑全部既有測試確認沒回歸

Run: `pytest -q`
Expected: 全綠（先前 ~926 + 4 = ~930）

### Step 7：Commit

```bash
git add web/app.py web/templates/trends.html tests/integration/test_e2e_trends.py
git commit -m "feat(trends): GET /trends route + minimal template (4 query params)"
```

---

## Task 6：trends.html sparkline UI（Chart.js 整合 + cam 卡片）

**Files:**
- Modify: `web/templates/trends.html`（大幅擴充）
- Modify: `tests/integration/test_e2e_trends.py`（加 3 個 template render 測試）

### Step 1：加 3 個測試

加入 `tests/integration/test_e2e_trends.py`：

```python
class TestTrendsTemplateRendering:
    """驗證 trends.html 結構（spec §4.3 / §7.2）。"""

    def _seed_one_cam_with_health(self, db_path: str, cam_id: str = "d-1") -> None:
        """塞 1 NVR + 1 cam + 1 筆 record。"""
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, ?, 'Cam1', 0, '2026-08-05T00:00:00Z')",
            (nvr_int, cam_id),
        )
        conn.execute(
            "INSERT INTO image_health_checks "
            "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
            "VALUES (?, ?, '2026-08-05T12:00:00Z', ?, '[]')",
            (cam_id, nvr_int, json.dumps({"is_frozen": False, "is_underexposed": False})),
        )
        conn.commit()
        conn.close()

    def test_with_seeded_data_shows_bins(self, flask_client):
        """seed image_health → 模板含 chart canvas。"""
        client, db_path = flask_client
        self._seed_one_cam_with_health(db_path)

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        assert "<canvas" in body, "應含 Chart.js canvas"
        # Chart.js CDN script（沿用 fleet.html 的 4.4.0）
        assert "chart.js" in body.lower() or "Chart.js" in body
        assert "Cam1" in body, "應顯示 cam 名稱"

    def test_abnormal_badge_for_3plus_abnormal_bins(self, flask_client):
        """abnormal_bins >= 1 的 cam 顯示紅色 badge 樣式。"""
        client, db_path = flask_client
        self._seed_one_cam_with_health(db_path, "d-bad")
        # seed 3 筆 frozen → 3 bin abnormal
        for h in (4.0, 5.0, 6.0):
            from datetime import datetime, timedelta, timezone
            checked = (datetime.now(timezone.utc) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO image_health_checks "
                "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
                "VALUES ('d-bad', NULL, ?, ?, '[]')",
                (checked, json.dumps({"is_frozen": True, "is_underexposed": False})),
            )
            conn.commit()
            conn.close()

        r = client.get("/trends")
        body = r.data.decode("utf-8")
        # 紅色框線 badge（class 標記）
        assert "abnormal" in body.lower(), "異常 cam 應有 abnormal CSS class"

    def test_empty_db_shows_empty_state(self, flask_client):
        """空 DB → 顯示「目前沒有 cam 紀錄」。"""
        client, _ = flask_client
        r = client.get("/trends")
        body = r.data.decode("utf-8")
        assert "沒有" in body or "no cam" in body.lower() or "empty" in body.lower()
```

### Step 2：跑測試確認 3 fail

Run: `pytest tests/integration/test_e2e_trends.py::TestTrendsTemplateRendering -v`
Expected: 3 failed（template 還沒有 canvas/abnormal class/empty state）

### Step 3：擴充 `web/templates/trends.html`（含 Chart.js）

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>Cam 健康趨勢</title>
  <!-- Chart.js 4.4.0（沿用 fleet.html 既有 CDN） -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; }
    .filters { margin: 16px 0; padding: 12px; background: #f8fafc; border-radius: 6px; }
    .filters a { padding: 6px 12px; margin-right: 8px; text-decoration: none;
                  border: 1px solid #cbd5e1; border-radius: 4px; color: #334155; }
    .filters a.active { background: #2563eb; color: white; }
    .filters select { padding: 6px 12px; margin-right: 8px; }
    .cam-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 16px; margin-top: 16px; }
    .cam-card { padding: 12px; border: 1px solid #e2e8f0; border-radius: 6px;
                background: white; }
    .cam-card.abnormal { border: 2px solid #ef4444; }
    .cam-card h3 { margin: 0 0 4px 0; font-size: 16px; }
    .cam-card .meta { font-size: 12px; color: #64748b; margin-bottom: 8px; }
    .cam-card .badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
                       font-size: 11px; background: #ef4444; color: white; }
    .cam-card .badge.zero { background: #22c55e; }
    .cam-canvas-wrapper { height: 80px; }
    .cam-detail { display: none; margin-top: 12px; }
    .cam-detail.expanded { display: block; }
    .cam-detail .cam-canvas-wrapper { height: 200px; }
    .empty { padding: 32px; text-align: center; color: #64748b;
             border: 1px dashed #cbd5e1; border-radius: 6px; margin-top: 16px; }
  </style>
</head>
<body>
  <h2>📈 Cam 健康趨勢</h2>
  <p>共 {{ summaries|length }} 台 cam（{{ range_hours }}h 視窗）</p>

  <!-- Filters -->
  <div class="filters">
    <span>視窗：</span>
    <a href="?{% if current_nvr %}nvr_id={{ current_nvr }}&{% endif %}{% if current_status != 'any' %}status={{ current_status }}&{% endif %}range=24h"
       class="{% if range_hours == 24 %}active{% endif %}">24h</a>
    <a href="?{% if current_nvr %}nvr_id={{ current_nvr }}&{% endif %}{% if current_status != 'any' %}status={{ current_status }}&{% endif %}range=7d"
       class="{% if range_hours == 168 %}active{% endif %}">7d</a>

    {% if nvrs %}
    <select onchange="window.location.href='?' + (this.value ? 'nvr_id=' + this.value : '') + '&range={{ '7d' if range_hours == 168 else '24h' }}'">
      <option value="">所有 NVR</option>
      {% for n in nvrs %}
      <option value="{{ n.nvr_id }}" {% if current_nvr == n.nvr_id %}selected{% endif %}>
        {{ n.name }}
      </option>
      {% endfor %}
    </select>
    {% endif %}

    <label>
      <input type="checkbox" onchange="
        window.location.href='?' + ('{% if current_nvr %}nvr_id={{ current_nvr }}&{% endif %}range={{ '7d' if range_hours == 168 else '24h' }}&') + (this.checked ? 'status=abnormal_only' : 'status=any')
      " {% if current_status == 'abnormal_only' %}checked{% endif %}>
      只看異常
    </label>
  </div>

  {% if not summaries %}
  <div class="empty">
    {% if current_nvr or current_status == 'abnormal_only' %}
      目前篩選條件下沒有 cam 紀錄。
    {% else %}
      目前沒有 cam 紀錄（DB 為空或 batch_scan 從未跑過）。
    {% endif %}
  </div>
  {% else %}
  <div class="cam-grid">
  {% for s in summaries %}
    <div class="cam-card {% if s.abnormal_bins > 0 %}abnormal{% endif %}"
         data-cam-id="{{ s.cam_id }}"
         onclick="toggleDetail(this)">
      <h3>{{ s.cam_name }}</h3>
      <div class="meta">
        NVR: {{ s.nvr_name }} |
        {% if s.abnormal_bins > 0 %}
          <span class="badge">{{ s.abnormal_bins }} 異常時數</span>
        {% else %}
          <span class="badge zero">健康</span>
        {% endif %}
      </div>
      <div class="cam-canvas-wrapper">
        <canvas id="chart-{{ s.cam_id }}"></canvas>
      </div>
      <div class="cam-detail">
        <div class="cam-canvas-wrapper">
          <canvas id="chart-detail-{{ s.cam_id }}"></canvas>
        </div>
      </div>
    </div>
  {% endfor %}
  </div>
  {% endif %}

  <script>
    // Mini sparkline 資料
    const summaries = {{ summaries|tojson }};
    const COLORS = {
      online: '#22c55e',
      frozen: '#f59e0b',
      underexposed: '#ef4444',
    };

    function makeChart(canvas, detail) {
      return new Chart(canvas, {
        type: 'line',
        data: {
          labels: [],
          datasets: [
            { label: 'Online', data: [], borderColor: COLORS.online, borderWidth: 1.5, pointRadius: 0 },
            { label: 'Frozen', data: [], borderColor: COLORS.frozen,  borderWidth: 1.5, pointRadius: 0 },
            { label: 'Underexposed', data: [], borderColor: COLORS.underexposed, borderWidth: 1.5, pointRadius: 0 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          legend: { display: detail },
          tooltips: { enabled: detail },
          scales: {
            y: { min: 0, max: 100, ticks: { display: detail } },
            x: { ticks: { display: detail } },
          },
          elements: { point: { radius: 0 }, line: { borderWidth: 1.5 } },
        },
      });
    }

    function populateChart(chart, bins, detail) {
      chart.data.labels = bins.map(b => b.start_utc.slice(11, 16));  // HH:MM
      chart.data.datasets[0].data = bins.map(b => b.online_pct);
      chart.data.datasets[1].data = bins.map(b => b.frozen_pct);
      chart.data.datasets[2].data = bins.map(b => b.underexposed_pct);
      chart.options.legend.display = detail;
      chart.update();
    }

    // Init
    document.addEventListener('DOMContentLoaded', () => {
      summaries.forEach(s => {
        const canvasMini = document.getElementById('chart-' + s.cam_id);
        const canvasDetail = document.getElementById('chart-detail-' + s.cam_id);
        if (canvasMini) populateChart(makeChart(canvasMini, false), s.bins, false);
        if (canvasDetail) populateChart(makeChart(canvasDetail, true), s.bins, true);
      });
    });

    function toggleDetail(card) {
      const detail = card.querySelector('.cam-detail');
      detail.classList.toggle('expanded');
    }
  </script>
</body>
</html>
```

### Step 4：跑測試確認 3 個 pass

Run: `pytest tests/integration/test_e2e_trends.py::TestTrendsTemplateRendering -v`
Expected: `3 passed`

### Step 5：跑全部測試

Run: `pytest -q`
Expected: 全綠（先前 ~930 + 3 = ~933）

### Step 6：Commit

```bash
git add web/templates/trends.html tests/integration/test_e2e_trends.py
git commit -m "feat(trends): sparkline grid template with Chart.js inline init"
```

---

## Task 7：trends.html 進階 filter 行為（route test 補強）

**Files:**
- Modify: `tests/integration/test_e2e_trends.py`

### Step 1：寫 2 個 route 測試

加入 `tests/integration/test_e2e_trends.py`：

```python
class TestTrendsRouteFilters:
    def test_range_7d_query(self, flask_client):
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')")
        conn.commit()
        conn.close()
        r = client.get("/trends?range=7d")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        # 7d 按鈕應 active（style 套用）
        assert "168" in body or "7d" in body

    def test_status_filter_abnormal_only(self, flask_client):
        client, db_path = flask_client
        conn = sqlite3.connect(db_path)
        nvr_int = conn.execute(
            "INSERT INTO nvr_servers (nvr_id, name) VALUES ('nvr-a', 'ACC-8')"
        ).lastrowid
        conn.execute(
            "INSERT INTO cameras (nvr_id, device_id, camera_name, is_ghost, last_seen_at) "
            "VALUES (?, 'd-healthy', 'HealthyCam', 0, '2026-08-05T00:00:00Z')",
            (nvr_int,),
        )
        conn.commit()
        conn.close()
        r = client.get("/trends?status=abnormal_only")
        assert r.status_code == 200
        # 沒 abnormal → 看不到 HealthyCam
        body = r.data.decode("utf-8")
        assert "HealthyCam" not in body, \
            "abnormal_only 應過濾掉 0 異常的 cam"
```

### Step 2：跑測試確認 2 個 pass（template 已支援 query）

Run: `pytest tests/integration/test_e2e_trends.py::TestTrendsRouteFilters -v`
Expected: `2 passed`

如果 fail，回頭補 template 邏輯。

### Step 3：Commit

```bash
git add tests/integration/test_e2e_trends.py
git commit -m "test(trends): route filter coverage - 7d range + abnormal_only"
```

---

## Task 8：base.html navbar 加 `📈 健康趨勢` 連結

**Files:**
- Modify: `web/templates/base.html`

### Step 1：手動驗證 navbar 結構

讀 `web/templates/base.html`，找 navbar 段（搜尋「navbar」、「nav-link」、其他現有 tab 如「異常」、「設備」）。

### Step 2：用 grep 找現有 navbar pattern 並複製

例如找「異常」連結，複製 1 行改為「📈 健康趨勢 → /trends」。

### Step 3：加連結

範例（依實際 navbar 結構調整）：

```html
<li class="nav-item">
  <a class="nav-link" href="/trends">📈 健康趨勢</a>
</li>
```

放在「異常」與「NVR」之間（合 user 直覺）。

### Step 4：手動驗證

Run: 啟動 8444 server，登入，確認任意已登入頁面的 navbar 都有「📈 健康趨勢」連結。

不需要新增測試（template 用現有 base.html 自動 extend，trends.html 也用 base.html 自動套用）。

### Step 5：Commit

```bash
git add web/templates/base.html
git commit -m "feat(trends): navbar link to /trends"
```

---

## Task 9：devices.html 每台 cam row 加 📈 連結

**Files:**
- Modify: `web/templates/devices.html`
- Create: `tests/test_trends_deeplinks_devices.py`（1 個測試）

### Step 1：寫 1 個測試

`tests/test_trends_deeplinks_devices.py`：

```python
"""驗證 devices.html 每台 cam row 有 📈 連結到 /trends。"""
from pathlib import Path
import re

DEVICES_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "devices_list.html"


def test_devices_html_has_trends_deep_link_for_each_cam():
    """devices_list.html 在 cam 名附近的 anchor 應含 /trends?cam_id= 或類似 pattern。

    用 grep 找「/trends」字串在 template 內，並驗證至少 1 個含 cam_id 查詢參數的 anchor。
    """
    content = DEVICES_HTML.read_text(encoding="utf-8")
    # 找 /trends?... 的 anchor
    pattern = re.compile(
        r'href\s*=\s*["\']/trends\?[^"\']*cam_id[^"\']*["\']',
        re.IGNORECASE,
    )
    assert pattern.search(content), \
        f"devices_list.html 應含至少 1 個 /trends?cam_id=... 的 deep link anchor，got:\n{content[:500]}"
```

### Step 2：跑測試確認 fail

Run: `pytest tests/test_trends_deeplinks_devices.py -v`
Expected: failed（devices_list.html 還沒有 deep link）

### Step 3：devices_list.html 加 deep link

讀 `web/templates/devices_list.html`（不是 devices.html）。找 cam 名 render 處（搜尋 `camera_name` 或 `cam.name`）。在該 td 後加：

```html
<a href="/trends?cam_id={{ cam.device_id }}&range=24h" title="查看健康趨勢">📈</a>
```

### Step 4：跑測試確認 pass

Run: `pytest tests/test_trends_deeplinks_devices.py -v`
Expected: `1 passed`

### Step 5：Commit

```bash
git add web/templates/devices_list.html tests/test_trends_deeplinks_devices.py
git commit -m "feat(trends): deep link from devices_list.html per cam row"
```

---

## Task 10：dashboard.html 異常 cam 列表加 📈 連結

**Files:**
- Modify: `web/templates/dashboard.html`
- Create: `tests/test_trends_deeplinks_dashboard.py`（1 個測試）

### Step 1：寫測試

`tests/test_trends_deeplinks_dashboard.py`：

```python
"""驗證 dashboard.html 異常 cam 列表有 📈 連結到 /trends。"""
from pathlib import Path
import re

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "dashboard.html"


def test_dashboard_html_has_trends_deep_link():
    """dashboard.html 異常 cam 段應含 /trends?...cam_id=... anchor。"""
    content = DASHBOARD_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        r'href\s*=\s*["\']/trends\?[^"\']*',
        re.IGNORECASE,
    )
    assert pattern.search(content), \
        f"dashboard.html 應含 /trends? 的 deep link，got:\n{content[:500]}"
```

### Step 2：跑測試確認 fail

Run: `pytest tests/test_trends_deeplinks_dashboard.py -v`
Expected: failed

### Step 3：dashboard.html 加 deep link

讀 dashboard.html，找異常 cam 清單（搜尋「abnormal」、「events」段）。在 cam 名後加：

```html
<a href="/trends?cam_id={{ event.device_id }}&range=24h" title="查看健康趨勢">📈</a>
```

### Step 4：跑測試確認 pass

Run: `pytest tests/test_trends_deeplinks_dashboard.py -v`
Expected: `1 passed`

### Step 5：Commit

```bash
git add web/templates/dashboard.html tests/test_trends_deeplinks_dashboard.py
git commit -m "feat(trends): deep link from dashboard.html abnormal cam list"
```

---

## Task 11：coverage.html 綠帶上方加 📈 連結

**Files:**
- Modify: `web/clips_templates/coverage.html`
- Create: `tests/test_trends_deeplinks_coverage.py`（1 個測試）

### Step 1：寫測試

`tests/test_trends_deeplinks_coverage.py`：

```python
"""驗證 coverage.html 綠帶上方 cam 名有 📈 連結到 /trends。"""
from pathlib import Path
import re

COVERAGE_HTML = (
    Path(__file__).resolve().parent.parent
    / "web" / "clips_templates" / "coverage.html"
)


def test_coverage_html_has_trends_deep_link():
    """coverage.html 應含 /trends?cam_id=... 的 deep link。"""
    content = COVERAGE_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        r'href\s*=\s*["\']/trends\?[^"\']*',
        re.IGNORECASE,
    )
    assert pattern.search(content), \
        f"coverage.html 應含 /trends? 的 deep link，got:\n{content[:500]}"
```

### Step 2：跑測試確認 fail

Run: `pytest tests/test_trends_deeplinks_coverage.py -v`
Expected: failed

### Step 3：coverage.html 加 deep link

讀 coverage.html，找 cam 名 render 處。在 cam 名旁邊加：

```html
<a href="/trends?cam_id={{ cam.device_id }}&range=24h" title="查看健康趨勢">📈</a>
```

### Step 4：跑測試確認 pass

Run: `pytest tests/test_trends_deeplinks_coverage.py -v`
Expected: `1 passed`

### Step 5：Commit

```bash
git add web/clips_templates/coverage.html tests/test_trends_deeplinks_coverage.py
git commit -m "feat(trends): deep link from coverage.html cam name"
```

---

## Task 12：視覺驗證（手動 + Playwright）

**Files:** 無檔案改動，純驗證

### Step 1：手動確認 pytest 全綠

Run: `pytest -q`
Expected: 全部綠（先前 ~933 + 4 個 deeplink 測試 = ~937）

### Step 2：語法檢查

```bash
python -m py_compile web/trends.py web/app.py
```

Expected: 無 error。

### Step 3：手動啟動 8444 + 用瀏覽器截圖

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8444 | Select-Object OwningProcess | Stop-Process -Force"
NVR_DB_PATH=./nvr_scan.db python web/app.py 8444 &
# 等 3 秒
Start-Sleep 3
# 開 Chrome 到 http://127.0.0.1:8444/trends
# 截圖 trends_full.png（C:\cc\NVR\）
```

預期：sparkline grid 顯示。沒資料則空狀態提示。

### Step 4：seed 假資料 + 重截

沒有真 NVR 資料 → 用 Python 直接 seed image_health_checks：

```python
# scripts/seed_trends_demo.py（暫存，跑完可刪）
import json, sqlite3
from datetime import datetime, timedelta, timezone
db = "nvr_scan.db"
conn = sqlite3.connect(db)
now = datetime.now(timezone.utc)
# 找 1 台 cam
row = conn.execute("SELECT device_id FROM cameras WHERE is_ghost=0 LIMIT 1").fetchone()
cam = row["device_id"]
# seed 24 筆：前 5 bin frozen spike
for h in range(24):
    checked = (now - timedelta(hours=24-h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    is_frozen = (4 <= h <= 7)
    is_underexposed = (10 <= h <= 12)
    metrics = {"is_frozen": is_frozen, "is_underexposed": is_underexposed,
               "blur_var": 100, "mean_luma": 0.5}
    conn.execute(
        "INSERT INTO image_health_checks (camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json) "
        "VALUES (?, NULL, ?, ?, '[]')",
        (cam, checked, json.dumps(metrics)),
    )
conn.commit()
conn.close()
print("seeded")
```

Run: `python scripts/seed_trends_demo.py`
然後 reload `/trends` → 看到 3 條 sparkline（綠/橘/紅），第 5-7 bin 橘色飆高，第 10-12 bin 紅色飆高。

### Step 5：點 sparkline → 展開詳細 chart 截圖

互動測試：點任一 cam card，確認 detail chart 展開（高度 80 → 200px），legend 顯示。

### Step 6：切 24h ↔ 7d → 截圖

點 24h / 7d 按鈕，URL 變化、bin 數 24 → 7、軸刻度調整。

### Step 7：套用「只看異常」篩選 → 截圖

勾選 checkbox → abnormal_only 的 cam 仍顯示、healthy 的 cam 過濾掉。

### Step 8：開 dashboard + 點 cam 行 📈 → 跳到 /trends

驗證 deeplink 點下去真的能跳。

### Step 9：截圖存檔 + commit

```bash
mkdir -p docs/img/trends
# 從瀏覽器手動截圖存到 docs/img/trends/：
# - trends_full_24h.png
# - trends_full_7d.png
# - trends_abnormal_only.png
# - trends_sparkline_expanded.png

git add docs/img/trends/
git commit -m "docs(trends): add /trends visual verification screenshots"
```

（如果截圖太大或不便，把 docs/img/trends 加進 .gitignore 維持略過也行——決定後 commit/或忽略）

---

## Self-Review（寫完 plan 後）

### 1. Spec coverage

| Spec 區段 | Plan Task |
|---|---|
| §2.1 健康三維度（online/frozen/dark） | Task 1-3（rename dark → underexposed） |
| §2.2 Schema：零改動 | 全部 task 用既有 schema，未 ALTER TABLE |
| §2.3 範圍 | Task 1-11（含 inline expand / range toggle / NVR filter / status filter / 排序 / 異常高亮） |
| §2.3 不做：modal / polling / CSV / events overlay / 12 theme 色調 / 跨 NVR 排行 / 自訂 threshold | Plan 全程未實作這些 |
| §2.4 與既有頁面整合（devices / dashboard / coverage / navbar） | Task 8-11 |
| §4.1 web/trends.py 兩個 dataclass + 兩個函式 | Task 1（dataclass）+ Task 4（summary） |
| §4.2 /trends route | Task 5 |
| §4.3 trends.html 結構 | Task 6 |
| §4.4 Chart.js 設定 | Task 6（CDN 沿用 fleet.html 4.4.0、Y 軸 0-100、pointRadius 0） |
| §5 資料流程 | Task 1 內 compute_health_timeseries 演算法 |
| §6 錯誤處理 | Task 1（flags_json fallback）+ Task 5（range fallback） |
| §7.1 純函式測試 | Task 1+2+3+4（13 項） |
| §7.2 Route 整合測試 | Task 5+7（9 項）+ DeepLink 3 項 = 12 項 |

### 2. Placeholder scan

- 無 "TODO" / "TBD" / "fill in later"
- `_get_db_path_for_trends()` 內部用既有 `_get_db_path(app)` helper，import 順序以網站既有風格為準（Step 4 註明「視 web/app.py 既有 helper 命名」）
- 若既有 helper 命名不同，implementer 須 Read web/app.py 前 100 行確認

### 3. Type consistency

- `HealthBin.start_utc: str` → Task 6 template 內 `b.start_utc.slice(11, 16)` 處理 ✅
- `CamHealthSummary.cam_id/cam_name/nvr_id/nvr_name/bins/abnormal_bins` → Task 4 + 6 一致 ✅
- `compute_health_timeseries()` 回傳 `list[HealthBin]` → Task 1 用 `assert len(bins) == 24` ✅
- `get_all_cams_health_summary()` 回傳 `list[CamHealthSummary]` → Task 4 一致 ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-05-cam-health-trends.md`.

Two execution options:
1. **Subagent-Driven (recommended)** — dispatch fresh subagent per task with two-stage review (spec compliance + code quality)
2. **Inline Execution** — run tasks in this session via executing-plans skill

⚠️ **Spec 修正提示**：本 plan 與原始 spec 在「dark / underexposed 命名」與「flags_json vs metrics_json」有差異。建議實作 spec review 時一併補上 spec 文件勘誤（建議 commit：`docs(changelog): record spec G metrics_json source correction`）。

Which approach?
