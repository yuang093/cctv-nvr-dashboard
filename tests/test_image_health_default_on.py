"""
tests/test_image_health_default_on.py
======================================
2026-07-30 Bug fix（/wall 縮圖沒有畫面）：

`_IMAGE_HEALTH_ENABLED` 預設應為 True。原因：
  - /wall 縮圖依賴 image_health_loop 內的 upsert_snapshot 寫入
  - 預設關閉 → Web UI /scan 永遠不會寫 snapshot → /wall 永遠顯示 placeholder
  - 修法：env NVR_IMAGE_HEALTH 未設或 != "0" 時，預設開啟
  - 保留 opt-out：用 NVR_IMAGE_HEALTH=0 可關閉

對齊 user 決策（option A 預設開啟）。
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _reload_batch_scan():
    """重新載入 batch_scan module（讓 module-load 時讀到的 env 生效）。"""
    if "batch_scan" in sys.modules:
        del sys.modules["batch_scan"]
    return importlib.import_module("batch_scan")


# === 1. 預設開啟 ===
def test_image_health_enabled_default_true_when_env_unset(monkeypatch):
    """NVR_IMAGE_HEALTH 未設 → _IMAGE_HEALTH_ENABLED 應為 True。

    理由：/wall 縮圖需要 image_health_loop 寫入 snapshot，
    預設關閉會讓 Web UI /scan 永遠沒縮圖。
    """
    monkeypatch.delenv("NVR_IMAGE_HEALTH", raising=False)
    bs = _reload_batch_scan()
    assert bs._IMAGE_HEALTH_ENABLED is True, (
        "image_health 預設應為 True（/wall 縮圖需要）"
    )


def test_image_health_enabled_default_true_when_env_empty(monkeypatch):
    """NVR_IMAGE_HEALTH=""（空字串） → True。"""
    monkeypatch.setenv("NVR_IMAGE_HEALTH", "")
    bs = _reload_batch_scan()
    assert bs._IMAGE_HEALTH_ENABLED is True


def test_image_health_enabled_explicit_one(monkeypatch):
    """NVR_IMAGE_HEALTH=1（明確開） → True。"""
    monkeypatch.setenv("NVR_IMAGE_HEALTH", "1")
    bs = _reload_batch_scan()
    assert bs._IMAGE_HEALTH_ENABLED is True


# === 2. opt-out 保留 ===
def test_image_health_enabled_opt_out_via_zero(monkeypatch):
    """NVR_IMAGE_HEALTH=0（明確關） → False。

    保留 opt-out 介面：避免 NVR 暫時不穩時拖累 scan。
    """
    monkeypatch.setenv("NVR_IMAGE_HEALTH", "0")
    bs = _reload_batch_scan()
    assert bs._IMAGE_HEALTH_ENABLED is False


# === 3. 等價切換：跟 timeline 同步 ===
def test_timeline_default_unchanged(monkeypatch):
    """本 fix 只動 image_health；timeline 預設仍為 False（需 NVR_TIMELINE=1）。"""
    monkeypatch.delenv("NVR_TIMELINE", raising=False)
    bs = _reload_batch_scan()
    assert bs._TIMELINE_ENABLED is False, "timeline 不在本 fix 範圍內"
