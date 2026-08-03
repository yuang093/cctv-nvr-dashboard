"""
web/snapshot.py
===============
純函式 JPEG 縮圖工具（給 /wall 縮圖快取 + 8555 clips 共用）。

從 web/clips_app.py 的 _compress_to_thumbnail 抽出共用，理由：
  - 8444 worker（image_health loop）也要縮圖
  - 8555 clips app 也要縮圖
  - 兩邊共用同一個 size / quality，避免漂移

設計：
  - compress_to_thumbnail(jpeg_bytes, size=(160,120), quality=82) → bytes
    失敗時 fallback 回原 bytes（與 clips_app 既有行為一致；不 raise）
  - get_thumbnail_dimensions(jpeg_bytes) → (width, height)
    壞 bytes 會 raise PIL.UnidentifiedImageError（給 upsert_snapshot 顯式判斷）
"""
from __future__ import annotations

import io
from typing import Tuple

# PIL 可用性快取（測試可 monkeypatch 模擬「沒 PIL」情境）
try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False


def compress_to_thumbnail(
    jpeg_bytes: bytes,
    size: tuple[int, int] = (160, 120),
    quality: int = 82,
) -> bytes:
    """JPEG bytes → 縮圖 JPEG bytes。失敗時 fallback 回原 bytes。

    Args:
        jpeg_bytes: 來源 JPEG bytes。
        size: 縮圖目標 (width, height)；im.thumbnail 維持比例。
        quality: JPEG quality 1-95（82 = clips_app 既有規格）。

    Returns:
        縮圖 JPEG bytes。失敗時回傳原 bytes（避免 worker crash）。
    """
    if not _PIL_OK:
        return jpeg_bytes
    try:
        im = Image.open(io.BytesIO(jpeg_bytes))
        im.thumbnail(size)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return jpeg_bytes


def get_thumbnail_dimensions(jpeg_bytes: bytes) -> tuple[int, int]:
    """從 JPEG bytes 讀 (width, height)。

    給 upsert_snapshot 寫入 DB 的 width/height 欄位用。

    Raises:
        PIL.UnidentifiedImageError: bytes 不是有效影像。
    """
    if not _PIL_OK:
        raise RuntimeError("Pillow 未安裝")
    im = Image.open(io.BytesIO(jpeg_bytes))
    return im.size  # (width, height)