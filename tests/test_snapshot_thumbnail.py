"""
tests/test_snapshot_thumbnail.py
=================================
web.snapshot.compress_to_thumbnail 純函式測試。

從 clips_app._compress_to_thumbnail 抽出共用：
  - JPEG bytes → 縮圖 JPEG bytes
  - 失敗 fallback 原 bytes
  - 預設 size=(160,120), quality=82

新增：
  - 接受 quality 參數（給未來調參用）
  - 同步回傳 (width, height) via inspect JPEG 頭（給 upsert_snapshot 寫入 DB）
"""

from __future__ import annotations

import io

import pytest
from PIL import Image


# === 測試素材 ===
def _make_jpeg(
    width: int, height: int, color: tuple[int, int, int] = (100, 150, 200)
) -> bytes:
    """產生測試 JPEG bytes。"""
    im = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _jpeg_dimensions(jpeg_bytes: bytes) -> tuple[int, int]:
    """從 JPEG bytes 讀 (width, height)。"""
    im = Image.open(io.BytesIO(jpeg_bytes))
    return im.size


# === 1. 正常壓縮 ===
def test_compress_basic_returns_smaller_jpeg():
    """640x480 → 160x120 應明顯變小（quality 82 + 縮小）。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(640, 480)
    out = compress_to_thumbnail(src)

    assert isinstance(out, bytes)
    assert len(out) < len(src), f"縮圖應該比原圖小（src={len(src)}, out={len(out)}）"
    # 仍是 JPEG
    assert out[:2] == b"\xff\xd8"


def test_compress_default_size_is_160x120():
    """預設 size=(160,120)。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(1280, 720)  # 16:9
    out = compress_to_thumbnail(src)
    w, h = _jpeg_dimensions(out)
    # im.thumbnail 維持比例：max(160,120) 為 160，1280→160 等比 → 90
    assert (w, h) == (160, 90)


def test_compress_custom_size():
    """自訂 size=(80, 60)。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(640, 480)
    out = compress_to_thumbnail(src, size=(80, 60))
    w, h = _jpeg_dimensions(out)
    assert (w, h) == (80, 60)


def test_compress_custom_quality():
    """quality=30 應比 quality=95 小（粗略但可驗證）。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(640, 480)
    out_high = compress_to_thumbnail(src, quality=95)
    out_low = compress_to_thumbnail(src, quality=30)
    assert len(out_low) < len(out_high)


# === 2. 失敗 fallback ===
def test_compress_returns_original_when_pil_missing(monkeypatch):
    """PIL 缺 → 回原 bytes（不 raise）。"""
    from web import snapshot as wsnap

    monkeypatch.setattr(wsnap, "_PIL_OK", False)
    src = _make_jpeg(100, 100)
    out = wsnap.compress_to_thumbnail(src)
    assert out == src


def test_compress_returns_original_when_bytes_invalid():
    """壞 bytes → 回原 bytes（不 raise）。"""
    from web.snapshot import compress_to_thumbnail

    src = b"\x00\x01\x02\x03not-a-jpeg"
    out = compress_to_thumbnail(src)
    assert out == src


# === 3. 輔助：get_thumbnail_dimensions ===
def test_get_thumbnail_dimensions_returns_w_h():
    """JPEG bytes → (w, h) tuple。給 upsert_snapshot 寫入 DB 用。"""
    from web.snapshot import get_thumbnail_dimensions

    src = _make_jpeg(160, 120)
    w, h = get_thumbnail_dimensions(src)
    assert (w, h) == (160, 120)


def test_get_thumbnail_dimensions_raises_on_invalid():
    """壞 bytes → raise PIL.UnidentifiedImageError。"""
    from web.snapshot import get_thumbnail_dimensions

    with pytest.raises(Exception):
        get_thumbnail_dimensions(b"not-a-jpeg")


# === 4. 邊界 ===
def test_compress_already_small_image():
    """小於 size 的圖：im.thumbnail 不放大，原尺寸保留。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(80, 60)
    out = compress_to_thumbnail(src)
    w, h = _jpeg_dimensions(out)
    assert (w, h) == (80, 60)


def test_compress_landscape_to_landscape():
    """640x480 (4:3) → 160x120 維持 4:3。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(640, 480)
    out = compress_to_thumbnail(src)
    w, h = _jpeg_dimensions(out)
    assert (w, h) == (160, 120)


def test_compress_portrait_to_portrait():
    """480x640 (3:4) → 90x120（Pillow thumbnail 以 size.min 為基準）。"""
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg(480, 640)
    out = compress_to_thumbnail(src)
    w, h = _jpeg_dimensions(out)
    # Pillow im.thumbnail 演算法：max(w,h) 縮到 size 的 min(120) → 480/640*120=90
    assert (w, h) == (90, 120)
