"""
tests/test_clips_app_thumbnail_refactor.py
============================================
2026-07-29（Wall 縮圖重構）：clips_app 改 import 共用 compress_to_thumbnail 後功能仍正常。

regression 測試：
  - clips_app 內部仍可呼叫 _compress_to_thumbnail（向後相容別名）
  - 行為跟原 inline 實作一致
"""
from __future__ import annotations

import io

import pytest
from PIL import Image


def _make_jpeg(width: int = 640, height: int = 480) -> bytes:
    im = Image.new("RGB", (width, height), (100, 150, 200))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_clips_app_has_compress_to_thumbnail_alias():
    """clips_app 應暴露 _compress_to_thumbnail 別名（向後相容）。"""
    from web import clips_app
    assert hasattr(clips_app, "_compress_to_thumbnail")
    assert callable(clips_app._compress_to_thumbnail)


def test_compress_to_thumbnail_alias_works():
    """別名呼叫的結果應等於 web.snapshot.compress_to_thumbnail。"""
    from web import clips_app
    from web.snapshot import compress_to_thumbnail

    src = _make_jpeg()
    a = clips_app._compress_to_thumbnail(src)
    b = compress_to_thumbnail(src)
    assert a == b


def test_compress_to_thumbnail_alias_size_default():
    """預設 size=(160,120) 維持不變。"""
    from web import clips_app

    src = _make_jpeg(640, 480)
    out = clips_app._compress_to_thumbnail(src)
    im = Image.open(io.BytesIO(out))
    assert im.size == (160, 120)


def test_compress_to_thumbnail_alias_quality_82():
    """quality 應維持 82（既有規格）。"""
    from web import clips_app

    src = _make_jpeg()
    out = clips_app._compress_to_thumbnail(src)
    # 跟 snapshot.compress_to_thumbnail 對照
    from web.snapshot import compress_to_thumbnail
    assert out == compress_to_thumbnail(src)


def test_compress_to_thumbnail_fallback_on_invalid():
    """壞 bytes → 回原 bytes（fallback）。"""
    from web import clips_app

    src = b"\x00not-a-jpeg"
    out = clips_app._compress_to_thumbnail(src)
    assert out == src