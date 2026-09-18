"""
tests/test_event_label_i18n.py
==============================
Phase 2.8（Arisan）Phase #5 補：event_kind_catalog 表驅動 i18n。

目標：events / abnormal / dashboard 顯示時一律查 event_kind_catalog 拿 name_zh。
- 17 種 catalog 內 topic → 回傳 name_zh
- 不在 catalog 的 topic → fallback（顯示原文 + 不丟）
- STATE_CONNECTING（is_fault=0）仍能查到名稱（用於 badge 顯示）
- 大小寫 / 空白 容錯（trim 後查）

DB schema 已建好（event_kind_catalog 由 db/sqlite_writer.py seed 17 筆）。
新增 helper：web.db.get_event_label_zh(topic) — 統一入口。
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.db import get_event_label_zh


@pytest.fixture
def seeded_db():
    """灌好 catalog 17 筆 + 1 個未知 topic。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    w = SqliteWriter(db_path)
    yield db_path
    del w
    gc.collect()
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 1. 17 種 catalog 內 topic 全部回傳 name_zh ===
@pytest.mark.parametrize(
    "topic,expected_zh",
    [
        ("DEVICE_VIDEO_SIGNAL_LOST", "影像訊號斷線（黑畫面）"),
        ("DEVICE_TAMPERING", "破壞/遮蔽（場景改變）"),
        ("DEVICE_COMMUNICATION_LOST", "通訊中斷"),
        ("DEVICE_CONNECTION_ERROR", "連線錯誤"),
        ("DEVICE_LONG_FAILED", "長期失敗（拔線）"),
        ("DEVICE_DISCONNECTED", "斷線"),
        ("DEVICE_ANOMALY_START", "影像分析異常"),
        ("DEVICE_UNUSUAL_STARTED", "未預期活動"),
        ("STATE_DISCONNECTED", "斷線（攝影機無回應）"),
        ("STATE_NOT_RESPONDING", "無回應（攝影機 hang）"),
        ("STATE_FAILED", "連線失敗"),
        ("STATE_LONG_FAILED", "長期失敗（拔網路線）"),
        ("STATE_BAD_CERTIFICATE", "憑證錯誤"),
        ("STATE_AUTH_FAILED", "認證失敗（帳密錯）"),
        ("STATE_NETWORK_DOWN", "網路斷線"),
        ("STATE_TIMED_OUT", "連線逾時"),
        ("STATE_CONNECTING", "連線中（短暫狀態）"),
    ],
)
def test_event_label_zh_returns_catalog_name(seeded_db, topic, expected_zh):
    """17 種 catalog topic 全部回傳對應中文名。"""
    assert get_event_label_zh(seeded_db, topic) == expected_zh


# === 2. 未知 topic → fallback（回傳原文，不丟）===
def test_unknown_topic_falls_back_to_original(seeded_db):
    """不在 catalog 的 topic → 直接回傳原文（不要 raise、不要空字串）。"""
    assert get_event_label_zh(seeded_db, "SOME_FUTURE_EVENT") == "SOME_FUTURE_EVENT"
    assert get_event_label_zh(seeded_db, "FOO_BAR_BAZ") == "FOO_BAR_BAZ"


# === 3. STATE_CONNECTING 仍能查到（即使 is_fault=0）===
def test_state_connecting_returns_zh_label(seeded_db):
    """STATE_CONNECTING 雖 is_fault=0，但仍要查得到中文（用於 badge 顯示）。"""
    zh = get_event_label_zh(seeded_db, "STATE_CONNECTING")
    assert zh == "連線中（短暫狀態）"
    # 不應回傳原文
    assert zh != "STATE_CONNECTING"


# === 4. trim 容錯 ===
def test_topic_with_whitespace_trimmed(seeded_db):
    """topic 帶前後空白應被 trim 後再查。"""
    assert (
        get_event_label_zh(seeded_db, "  DEVICE_TAMPERING  ") == "破壞/遮蔽（場景改變）"
    )


# === 5. 空字串 fallback ===
def test_empty_topic_falls_back(seeded_db):
    """空字串 → 直接回傳空字串（不要查 catalog）。"""
    assert get_event_label_zh(seeded_db, "") == ""


# === 6. DB 不存在時不掛（給 legacy fallback 測試）===
def test_missing_db_returns_topic_as_is():
    """DB 檔不存在 → fallback 回傳原文（不 raise）。"""
    # 用一個絕對不存在的路徑
    result = get_event_label_zh(
        "/nonexistent/path/does_not_exist.db", "DEVICE_TAMPERING"
    )
    # 行為：file not found 不掛；fallback 到原 topic（或舊 get_topic_zh 規則）
    # 期望：至少不 raise，回傳非空字串
    assert isinstance(result, str)
    assert len(result) > 0
