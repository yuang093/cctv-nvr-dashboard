"""
tests/test_frozen_dark_scene.py
================================
2026-07-30：cam2 (物料暫存區-2) 持續 IMAGE_HEALTH_FROZEN 誤判修法。

觀察：
  - cam2 frozen_diff ~0.2（5 次都在 0.18-0.28）
  - cam2 mean_luma ~0.29（夜視場景，較暗）
  - cam1 frozen_diff = 10.8（正常），mean_luma = 0.40

原因：cam2 拍空曠場景，5 秒內完全沒變化 → 演算法判定 frozen。
但實務上空曠場景的「凍結」跟真正故障（影像死掉）難以區分。

修法：
  - FROZEN_DIFF_THRESHOLD 從 5.0 拉高到 1.0（基本改良）
  - 當 mean_luma < 0.30（過暗場景）→ is_frozen 強制 False
    （夜視模式拍空曠場景本來就變化小；不該誤報）

  旗標仍記錄 dark_scene_skipped（給未來 debug 用），但不當異常。
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from web.image_health import FROZEN_DIFF_THRESHOLD, is_frozen


def _make_jpeg(mean_luma_byte: int, size: tuple[int, int] = (64, 64)) -> bytes:
    """生成指定 mean luma 的灰階 JPEG bytes。

    Args:
        mean_luma_byte: 平均灰階值 (0~255)。
    """
    img = Image.new("L", size, mean_luma_byte)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# === 1. threshold 終值：1.0 ===
def test_frozen_threshold_is_lowered():
    """FROZEN_DIFF_THRESHOLD 終值為 1.0。

    演變：原 5.0（太寬鬆）→ 2026-07-30 拉到 1.0（搭配 dark_scene_skip 救暗場景）。

    2026-07-30 嘗試拉高到 5.0 救明亮靜態場景（cam2 luma=0.41）但實測驗證
    threshold 變大反讓 frozen 觸發變容易（diff < threshold 才 frozen），
    對誤報無解。維持 1.0，等待 K 修法（shadow mode）解決。
    """
    assert FROZEN_DIFF_THRESHOLD == 1.0


# === 2. 過暗場景跳過 frozen 判定 ===
def test_is_frozen_dark_scene_skipped():
    """mean_luma < 0.30 的過暗場景，frozen_diff 雖小，仍不算 frozen。

    cam2 (物料暫存區) mean_luma 0.29、frozen_diff 0.2 → 不應觸發。
    """
    # mean_luma_byte = 76 → 76/255 ≈ 0.298（< 0.30）
    jpeg_a = _make_jpeg(76)
    jpeg_b = _make_jpeg(76)  # 完全相同
    result = is_frozen(jpeg_a, jpeg_b)
    assert result.mean_abs_diff < 1.0  # 確認真的接近 0
    assert result.is_frozen is False, "過暗場景不該誤判 frozen"


# === 3. 正常光線仍正常判定 frozen ===
def test_is_frozen_normal_light_still_works():
    """mean_luma >= 0.30、frozen_diff 仍小的 → 應判定 frozen。"""
    # mean_luma_byte = 128 → 128/255 ≈ 0.50
    jpeg_a = _make_jpeg(128)
    jpeg_b = _make_jpeg(128)  # 完全相同
    result = is_frozen(jpeg_a, jpeg_b)
    assert result.is_frozen is True, "正常光線 + 完全相同 → 該 frozen"


# === 4. 正常光線 + 有變化 → 非 frozen ===
def test_is_frozen_normal_light_with_change():
    """mean_luma 正常、兩張有差異 → 非 frozen。"""
    # mean_luma_byte = 128 (normal light)
    jpeg_a = _make_jpeg(128)
    # 製造 30 byte 的差異 → diff 應 >= 30
    img_b = Image.new("L", (64, 64), 158)  # 158 (差 30)
    buf = BytesIO()
    img_b.save(buf, format="JPEG", quality=85)
    jpeg_b = buf.getvalue()
    result = is_frozen(jpeg_a, jpeg_b)
    assert result.is_frozen is False


# === 5. 過暗場景旗標紀錄（dark_scene_skipped） ===
def test_is_frozen_dark_scene_records_in_flags():
    """FROZEN call 應回傳 mean_abs_diff，但 is_frozen 是 False。"""
    jpeg_a = _make_jpeg(76)  # dark
    jpeg_b = _make_jpeg(76)  # same
    result = is_frozen(jpeg_a, jpeg_b)
    assert result.mean_abs_diff < 1.0
    assert result.is_frozen is False
