"""
tests/test_system_health.py
============================
2026-08-03（Spec C）：gateway 本機健康指標。

回傳 6 個指標：
  - cpu_percent: float, 0~100
  - ram_used_gb: float
  - ram_total_gb: float
  - ram_percent: float, 0~100
  - disk_used_gb: float
  - disk_total_gb: float
  - disk_percent: float, 0~100
  - proc_rss_mb: float（本 Flask process 的 resident memory）
  - uptime_seconds: int（從 process create_time 算到 now）
  - threads: int（本 process thread 數）

設計原則：
  - 不傳 OSError 給 caller（psutil 失敗 → 回 0/-1 標 unavailable）
  - 不做 cache（即時讀取，user 想知道「現在」狀態）
  - test 用 monkeypatch 替掉 psutil 函式，避免依賴本機實際硬體
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


def _make_fake_psutil(*, cpu: float, vmem, disk, proc):
    """模擬 psutil 模組的函式。"""
    fake = MagicMock()
    fake.cpu_percent.return_value = cpu
    fake.virtual_memory.return_value = vmem
    fake.disk_usage.return_value = disk
    fake.Process.return_value = proc
    return fake


@pytest.fixture
def fake_psutil(monkeypatch):
    """預設一個「中等負載」的本機：CPU 25%、RAM 8/16GB、Disk 100/500GB、process RSS 200MB。"""
    vmem = MagicMock(total=16 * 1024**3, used=8 * 1024**3, percent=50.0)
    disk = MagicMock(total=500 * 1024**3, used=100 * 1024**3, percent=20.0)
    proc = MagicMock(
        memory_info=lambda: MagicMock(rss=200 * 1024**2),
        create_time=lambda: time.time() - 3600,  # 1 小時前
        num_threads=lambda: 8,
    )
    fake = _make_fake_psutil(cpu=25.0, vmem=vmem, disk=disk, proc=proc)
    monkeypatch.setattr("web.fleet._psutil", fake)
    return fake


def test_system_health_basic(fake_psutil):
    from web.fleet import get_system_health
    h = get_system_health()
    assert h["cpu_percent"] == 25.0
    assert h["ram_total_gb"] == 16.0
    assert h["ram_used_gb"] == 8.0
    assert h["ram_percent"] == 50.0
    assert h["disk_total_gb"] == 500.0
    assert h["disk_used_gb"] == 100.0
    assert h["disk_percent"] == 20.0
    assert h["proc_rss_mb"] == 200.0
    assert 3590 < h["uptime_seconds"] < 3610  # 接近 3600
    assert h["threads"] == 8
    assert h["available"] is True


def test_system_health_psutil_oserror_returns_unavailable(monkeypatch):
    """psutil 任何函式拋 OSError → 回 available=False、其他欄位為 0。"""
    fake = MagicMock()
    fake.cpu_percent.side_effect = OSError("no /proc")
    fake.virtual_memory.side_effect = OSError("no /proc")
    fake.disk_usage.side_effect = OSError("no /proc")
    fake.Process.return_value.memory_info.side_effect = OSError("no /proc")
    fake.Process.return_value.create_time = 0
    fake.Process.return_value.num_threads = lambda: 0
    monkeypatch.setattr("web.fleet._psutil", fake)

    from web.fleet import get_system_health
    h = get_system_health()
    assert h["available"] is False
    assert h["cpu_percent"] == 0.0
    assert h["ram_total_gb"] == 0.0
    assert h["threads"] == 0


def test_system_health_high_load(fake_psutil):
    """高負載：CPU 95%、RAM 95% → 仍正常回傳。"""
    fake_psutil.cpu_percent.return_value = 95.0
    fake_psutil.virtual_memory.return_value = MagicMock(
        total=16 * 1024**3, used=15.2 * 1024**3, percent=95.0
    )
    from web.fleet import get_system_health
    h = get_system_health()
    assert h["cpu_percent"] == 95.0
    assert h["ram_percent"] == 95.0