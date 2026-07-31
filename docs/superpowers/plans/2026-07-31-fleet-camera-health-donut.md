# Plan: /fleet 相機健康分布 donut 圖（2026-07-31）

> **對應 spec**：`docs/superpowers/specs/2026-07-31-fleet-camera-health-donut-design.md`
> **Spec 對應藍圖**：021.PNG（4 項藍圖的「相機健康」項；其他 3 項仍 TODO）

**Goal**：在 `/fleet` 頁加一個 donut 圖，顯示跨 NVR 合計的 cam 健康分布（online / signal_lost / no_signal），並標出被過濾的 ghost cam 數。

**Architecture**：Chart.js 4.x CDN（純前端渲染 donut）；server-side 只負責計算 distribution dict。

**Tech Stack**：Chart.js 4.4.0 CDN + Flask template + Bootstrap 5.3.2（既有）。

---

### Task 1：TDD 寫 3 個失敗測試

**Files**:
- Create: `tests/test_fleet_camera_health_distribution.py`

- [ ] **Step 1：寫 test_health_distribution_basic**

```python
def test_health_distribution_basic():
    """3 台 cam：1 online / 1 signal_lost / 1 no_signal，回傳 1/1/1。"""
    with tempfile_db(["online", "signal_lost", "no_signal"]) as (db_path, w, nvr_int, rid):
        from web.fleet import get_camera_health_distribution
        d = get_camera_health_distribution(db_path)
        assert d["online"] == 1
        assert d["signal_lost"] == 1
        assert d["no_signal"] == 1
        assert d["total"] == 3
        assert d["ghost_count"] == 0
```

- [ ] **Step 2：寫 test_health_distribution_excludes_ghost**

```python
def test_health_distribution_excludes_ghost():
    """ghost cam 應不計入 distribution，但 ghost_count 應 = 1。"""
    with tempfile_db(["online", "ghost"]) as (db_path, w, nvr_int, rid):
        # 標記 ghost
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE cameras SET is_ghost = 1 WHERE device_id = 'ghost-1'")
        conn.commit()
        conn.close()
        from web.fleet import get_camera_health_distribution
        d = get_camera_health_distribution(db_path)
        assert d["online"] == 1
        assert d["total"] == 1
        assert d["ghost_count"] == 1
```

- [ ] **Step 3：寫 test_health_distribution_empty**

```python
def test_health_distribution_empty():
    """0 台 cam → 全 0。"""
    with tempfile_db([]) as (db_path, w, nvr_int, rid):
        from web.fleet import get_camera_health_distribution
        d = get_camera_health_distribution(db_path)
        assert d == {"online": 0, "signal_lost": 0, "no_signal": 0,
                     "total": 0, "ghost_count": 0}
```

- [ ] **Step 4：跑測試確認 RED**

```bash
python -m pytest tests/test_fleet_camera_health_distribution.py -v
# Expected: 3 failed (function not defined)
```

### Task 2：實作 get_camera_health_distribution

**Files**:
- Modify: `web/fleet.py`（加在檔尾）

- [ ] **Step 1：實作**

```python
def get_camera_health_distribution(db_path: str) -> dict:
    """跨 NVR 合計 cam 的健康分布（過濾 ghost cam）。

    Returns:
        {
            "online": int, "signal_lost": int, "no_signal": int,
            "total": int, "ghost_count": int,
        }
    """
    cams = get_wall_cameras_with_snapshots(db_path, filter_kind="all")
    online = sum(1 for c in cams if c["category"] == "online")
    signal_lost = sum(1 for c in cams if c["category"] == "signal_lost")
    no_signal = sum(1 for c in cams if c["category"] == "no_signal")
    return {
        "online": online,
        "signal_lost": signal_lost,
        "no_signal": no_signal,
        "total": online + signal_lost + no_signal,
        "ghost_count": _count_ghost_cameras(db_path),
    }


def _count_ghost_cameras(db_path: str) -> int:
    """輔助：總 ghost cam 數（被過濾的）。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM cameras WHERE is_ghost = 1").fetchone()[0]
    finally:
        conn.close()
```

- [ ] **Step 2：跑測試確認 GREEN**

```bash
python -m pytest tests/test_fleet_camera_health_distribution.py -v
# Expected: 3 passed
```

### Task 3：把 distribution 傳給 /fleet template

**Files**:
- Modify: `web/app.py` /fleet route

- [ ] **Step 1：找 /fleet route**

```bash
grep -n "fleet" /c/cc/NVR/web/app.py | head -5
```

- [ ] **Step 2：加 distribution dict 到 render context**

找到現有的 render_template("fleet.html", ...)，加 `health_dist=get_camera_health_distribution(db_path)`，並加 `from web.fleet import get_fleet_view, get_camera_health_distribution`（如果還沒）。

### Task 4：fleet.html 加 Chart.js CDN + canvas + legend

**Files**:
- Modify: `web/templates/fleet.html`

- [ ] **Step 1：在 `<head>` 加 Chart.js CDN**

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
```

- [ ] **Step 2：加 donut card row（伺服器卡 grid 之上）**

```html
<div class="row mb-3">
  <div class="col-md-4">
    <div class="card h-100">
      <div class="card-body">
        <h5>📊 相機健康分布</h5>
        <canvas id="health-donut" height="200"></canvas>
        <p class="text-center text-muted mt-2 mb-0">總計 {{ health_dist.total }} 台 cam</p>
      </div>
    </div>
  </div>
  <div class="col-md-8">
    <div class="card h-100">
      <div class="card-body">
        <h5>圖例</h5>
        <ul class="list-unstyled">
          <li><span class="badge bg-success">●</span> 在線 (online) — <strong>{{ health_dist.online }}</strong> 台
            <p class="text-muted small ms-4 mb-2">connection 正常 + 無異常事件</p>
          </li>
          <li><span class="badge bg-warning text-dark">●</span> 訊號斷 (signal_lost) — <strong>{{ health_dist.signal_lost }}</strong> 台
            <p class="text-muted small ms-4 mb-2">cam 仍在但無影像訊號</p>
          </li>
          <li><span class="badge bg-danger">●</span> 無訊號 (no_signal) — <strong>{{ health_dist.no_signal }}</strong> 台
            <p class="text-muted small ms-4 mb-2">cam 已 disconnect / 離線</p>
          </li>
        </ul>
        {% if health_dist.ghost_count > 0 %}
        <p class="small text-warning mt-2 mb-0">⚠ {{ health_dist.ghost_count }} 台 ghost cam（已被 NVR 移除）不計入</p>
        {% endif %}
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3：加 Chart.js init script（檔尾）**

```html
<script>
  const ctx = document.getElementById('health-donut').getContext('2d');
  new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['在線', '訊號斷', '無訊號'],
      datasets: [{
        data: [
          {{ health_dist.online }},
          {{ health_dist.signal_lost }},
          {{ health_dist.no_signal }},
        ],
        backgroundColor: ['#22c55e', '#f59e0b', '#ef4444'],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
      },
      cutout: '60%',
    },
  });
</script>
```

### Task 5：驗證 + 截圖

- [ ] **Step 1：跑全套 pytest**

```bash
python -m pytest tests/ -q --ignore=tests/integration
# Expected: 732 → 735 全綠
```

- [ ] **Step 2：重啟 8444**

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 8444 -State Listen | Select-Object -First 1 OwningProcess | Stop-Process -Force"
PYTHONPATH=. python -m web.app 8444 > nvr_web.out 2> nvr_web.err &
sleep 3
```

- [ ] **Step 3：manifold 驗證**

```bash
curl -sS http://127.0.0.1:8444/fleet | grep -E "health-donut|chart.js|相機健康"
```

- [ ] **Step 4：Playwright 截圖**

```bash
# 視覺驗證 donut 與圖例
```

- [ ] **Step 5：commit**

```bash
git add web/fleet.py web/app.py web/templates/fleet.html tests/test_fleet_camera_health_donut.py
git commit -m "feat(fleet): 加相機健康分布 donut 圖（Spec B 最小可行）"
```

---

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| Chart.js CDN 失效 | fallback 用本地化暫不處理（CDN 99% 穩） |
| donut 顯示 0/0/0 太空 | 加 "無資料" 文字 |
| donut 顯示 1/0/0 滿圓不漂亮 | cutout 60% 讓中間有空間 |
| template cache 沒重啟 | 強制重啟 8444 |

## 不做的事

- ❌ 雲端覆蓋圖
- ❌ 廢牌分布圖
- ❌ per-NVR stacked bar
- ❌ Chart.js 加進 base.html
- ❌ /fleet cache 改動
