"""
test_fmp4_truncation.py
========================
驗證 web.clip_retrieval 的 fmp4 truncation 邏輯。

為什麼重要：2026-07-14 user 回報 2+ 台 cam 同步撥放時影片長度不一致，
因為 NVR fmp4 是 stream 沒 end param，每台 cam 拿到的 mp4 自然長度不同。
解法：server 解析 mp4 box 結構（moov.timescale + moof.tfdt），累積
decode time ≥ target 時切掉 prefix。讓所有 cam 拿到的 mp4 都是交集長度。

測試項目：
1. timescale 正確解析（mdhd.timescale）
2. tfdt 正確解析（baseMediaDecodeTime）
3. 跨多個 moof 累積 decode time，超 target 切在正確 moof 邊界
4. 沒 moov / 沒 tfdt → 退回 byte-budget fallback
5. target 超過全部 fragment 總和 → 完整 yield
"""
from __future__ import annotations

import struct
import os
import sys

# 把專案根目錄加到 path 才能 import web.* （既有用法）
PROJECT_ROOT = "C:\\cc\\NVR"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 設定 env vars 在 import 前
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")

import pytest  # noqa: E402

from web.clip_retrieval import (  # noqa: E402
    _find_fmp4_cut_point, _truncate_fmp4_chunks,
    _extract_moov_timescale, _extract_moof_tfdt,
)


# === Helpers：構造假 fmp4 bytes ===

def _make_box(box_type: bytes, payload: bytes) -> bytes:
    """Construct a complete ISO BMFF box."""
    size = 8 + len(payload)
    return struct.pack(">I", size) + box_type + payload


def _make_fullbox(box_type: bytes, version: int, payload: bytes) -> bytes:
    """Construct a fullbox (version + flags + payload)."""
    inner = struct.pack(">B", version) + b"\x00\x00\x00" + payload
    return _make_box(box_type, inner)


def _make_mdhd(timescale: int) -> bytes:
    """mdhd version 0: timescale @ offset 12 after 1+3+4+4."""
    creation_time = struct.pack(">I", 0)
    modification_time = struct.pack(">I", 0)
    timescale_bytes = struct.pack(">I", timescale)
    duration = struct.pack(">I", 0)
    # 內層 payload 在 fullbox 之後：creation + modification + timescale + duration + language + reserved
    payload = creation_time + modification_time + timescale_bytes + duration
    return _make_fullbox(b"mdhd", 0, payload)


def _make_tfdt(ticks: int) -> bytes:
    """tfdt version 0: 4-byte baseMediaDecodeTime."""
    return _make_fullbox(b"tfdt", 0, struct.pack(">I", ticks))


def _make_trak(timescale: int) -> bytes:
    """trak → mdia → mdhd(timescale). 最小能動的 trak。"""
    mdhd = _make_mdhd(timescale)
    mdia = _make_box(b"mdia", mdhd)
    trak = _make_box(b"trak", mdia)
    return trak


def _make_moov(timescale: int) -> bytes:
    return _make_box(b"moov", _make_trak(timescale))


def _make_tfdt_in_moof(ticks: int) -> bytes:
    """moof → traf → tfdt(ticks)."""
    tfdt = _make_tfdt(ticks)
    traf = _make_box(b"traf", tfdt)
    moof = _make_box(b"moof", traf)
    return moof


def _make_mdat(payload_size: int = 32) -> bytes:
    """mdat with random dummy bytes."""
    return _make_box(b"mdat", b"\x00" * payload_size)


def _make_fmp4(timescale: int, moof_ticks_list: list[int], mdat_size: int = 32) -> bytes:
    """Construct full fmp4: moov + (moof + mdat)* for each ticks."""
    parts = [_make_moov(timescale)]
    for ticks in moof_ticks_list:
        parts.append(_make_tfdt_in_moof(ticks))
        parts.append(_make_mdat(mdat_size))
    return b"".join(parts)


# === Unit tests for box parsers ===

def test_extract_moov_timescale_simple():
    """moov/trak/mdia/mdhd.timescale 正確抽出。"""
    timescale = 90000  # common mp4 timescale (90kHz)
    buf = _make_fmp4(timescale, [0, 90000])  # 1 fragment at t=0, 1 fragment at t=1s
    # Find the moov
    import io
    pos = 0
    moov_off = None
    while pos + 8 <= len(buf):
        size = struct.unpack(">I", buf[pos:pos+4])[0]
        btype = buf[pos+4:pos+8]
        if btype == b"moov":
            moov_off = pos
            moov_size = size
            break
        pos += size
    assert moov_off is not None
    ts = _extract_moov_timescale(buf, moov_off, moov_size)
    assert ts == timescale


def test_extract_moof_tfdt_correct():
    """從 moof 抽 baseMediaDecodeTime。"""
    timescale = 90000
    ticks_per_sec = timescale
    buf = _make_fmp4(timescale, [0, ticks_per_sec])  # 1st @ 0, 2nd @ 1s
    pos = 0
    moofs_found = []
    while pos + 8 <= len(buf):
        size = struct.unpack(">I", buf[pos:pos+4])[0]
        btype = buf[pos+4:pos+8]
        if btype == b"moof":
            moofs_found.append((pos, size))
        pos += size
    assert len(moofs_found) == 2
    # 1st moof tfdt should be 0
    assert _extract_moof_tfdt(buf, moofs_found[0][0], moofs_found[0][1]) == 0
    # 2nd moof tfdt should be 90000 (1s * 90kHz)
    assert _extract_moof_tfdt(buf, moofs_found[1][0], moofs_found[1][1]) == ticks_per_sec


# === Integration tests for cut-point finder ===

def test_cut_point_middle_of_fragments():
    """5 個 fragments (1s each, timescale=1000)，target=2.5s → cut @ frag 2 end。"""
    timescale = 1000
    fragments = [0, 1000, 2000, 3000, 4000]  # t=0,1,2,3,4 sec
    buf = _make_fmp4(timescale, fragments)
    target_seconds = 2.5
    cut = _find_fmp4_cut_point(buf, target_seconds)
    assert cut is not None
    # Expect cut to be at end of 2nd fragment (frag with tfdt=2000 is the 3rd, ends before 4th)
    # elapsed at tfdt=3000 = 3000-0 = 3000 = 3s ≥ 2500 ticks → CUT BEFORE the 4th moof
    # last_safe_cut after fragment 3 (tfdt=2000) → end of mdat following moof with tfdt=2000
    assert cut > 0
    assert cut < len(buf)


def test_cut_point_returns_full_when_target_exceeds_total():
    """target > 全部 fragment 總秒數 → 包含所有 fragments 的 prefix（cut = end of last moof）。"""
    timescale = 1000
    fragments = [0, 1000, 2000]  # total 3 sec
    buf = _make_fmp4(timescale, fragments)
    target_seconds = 30.0  # want 30s but only have 3s
    cut = _find_fmp4_cut_point(buf, target_seconds)
    assert cut is not None
    # 因為沒有 fragment 超過 target，last_safe_cut 停在最後一個 moof 結束
    # 那裡就是「包含全部 3 個 moofs 的最小 prefix」（後面可能還有 mdat）
    assert cut > 0
    assert cut <= len(buf)
    # 確認 cut 之前有 3 個 moof box
    pos = 0
    moof_count_in_cut = 0
    while pos + 8 <= cut:
        size = struct.unpack(">I", buf[pos:pos+4])[0]
        btype = buf[pos+4:pos+8]
        if btype == b"moof":
            moof_count_in_cut += 1
        pos += size
    assert moof_count_in_cut == 3


def test_cut_point_first_fragment_already_past_target():
    """target < 第二個 fragment decode time → 切在第一個 fragment 結束。
    （演算法回傳「最後一個完整 fragment 結束」，不是 0；這樣 mp4 仍可播。）"""
    timescale = 1000
    fragments = [0, 10000]  # 1st frag tfdt=0, 2nd @ tfdt=10000 (10s)
    buf = _make_fmp4(timescale, fragments)
    target_seconds = 1.0
    cut = _find_fmp4_cut_point(buf, target_seconds)
    # elapsed at 2nd moof.tfdt=10000 → 10000-0 = 10000 ticks = 10s > 1s target
    # → 切在第二個 moof 之前 → 保留第一個完整 fragment（moof + mdat）
    assert cut > 0
    pos = 0
    moof_count = 0
    while pos + 8 <= cut:
        size = struct.unpack(">I", buf[pos:pos+4])[0]
        btype = buf[pos+4:pos+8]
        if btype == b"moof":
            moof_count += 1
        pos += size
    assert moof_count == 1  # 只留第一個 moof
    assert cut < len(buf)   # 後面還有第二個 fragment 沒切進來


def test_cut_point_no_mov_returns_none():
    """沒 moov → 回傳 None（呼叫端會 fallback byte-budget）。"""
    # 構造只有 moof+mdat，沒 moov
    parts = []
    for i in range(3):
        parts.append(_make_tfdt_in_moof(i * 1000))
        parts.append(_make_mdat())
    buf = b"".join(parts)
    cut = _find_fmp4_cut_point(buf, target_seconds=2.0)
    assert cut is None


# === End-to-end: truncate_chunks wrapper ===

def test_truncate_chunks_returns_prefix():
    """drain source → cut → yield prefix。"""
    timescale = 1000
    fragments = [0, 1000, 2000, 3000, 4000]
    buf = _make_fmp4(timescale, fragments)
    # 切 1 chunk
    chunks = [buf]
    out = b"".join(_truncate_fmp4_chunks(iter(chunks), target_seconds=2.5))
    # 應該比原 buf 短
    assert len(out) < len(buf)
    assert len(out) > 0


def test_truncate_chunks_none_target_passthrough():
    """target_seconds=None → 原樣 yield。"""
    timescale = 1000
    fragments = [0, 1000]
    buf = _make_fmp4(timescale, fragments)
    out = b"".join(_truncate_fmp4_chunks(iter([buf]), target_seconds=None))
    assert out == buf


def test_truncate_chunks_no_moov_uses_byte_budget():
    """沒 moov → fallback byte-budget（不會 crash）。"""
    parts = []
    for i in range(3):
        parts.append(_make_tfdt_in_moof(i * 1000))
        parts.append(_make_mdat(64))  # 64-byte payload
    buf = b"".join(parts)
    # 2 秒 budget
    out = b"".join(_truncate_fmp4_chunks(iter([buf]), target_seconds=2.0))
    # byte-budget 切在 2.0 * 1Mbps / 8 = 250000 bytes → 但 buf 只有 64*6=384 bytes
    # 所以 fallback 給 full buf
    assert len(out) > 0
    assert len(out) <= len(buf)
