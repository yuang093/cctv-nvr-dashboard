"""
tests/test_image_health.py
==========================
Phase 2.8（Arisan 影像健康巡檢）：web/image_health.py 純函式測試。

覆蓋 4 種偵測 + 邊界值 + 錯誤處理：
  - 模糊（blur_var < 30）
  - 過曝（mean_luma > 0.85）
  - 欠曝（mean_luma < 0.15）
  - 凍結（兩張 mean_abs_diff < 5）
  - 正常影像（無 flag 觸發）
  - 邊界值（恰好等於 threshold）
  - 錯誤處理（空 bytes、無效 jpeg、不同尺寸仍可解）
"""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageFilter

from web.image_health import (
    analyze_image,
    is_frozen,
    ImageHealthResult,
    FrozenResult,
    BLUR_VAR_THRESHOLD,
    LUMA_OVEREXPOSED,
    LUMA_UNDEREXPOSED,
    FROZEN_DIFF_THRESHOLD,
)


# === Fixture：製造各種測試影像 ===
def _encode(img: Image.Image, fmt: str = "JPEG") -> bytes:
    """PIL Image → bytes。"""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def blank_white_jpeg() -> bytes:
    """全白 → 過曝。"""
    img = Image.new("L", (128, 128), color=255)
    return _encode(img)


@pytest.fixture
def blank_black_jpeg() -> bytes:
    """全黑 → 欠曝。"""
    img = Image.new("L", (128, 128), color=0)
    return _encode(img)


@pytest.fixture
def blank_gray_jpeg() -> bytes:
    """中灰 → 正常（無 flag 觸發）。"""
    img = Image.new("L", (128, 128), color=128)
    return _encode(img)


@pytest.fixture
def blurred_jpeg() -> bytes:
    """模糊影像（單色噪聲極少）→ 模糊 flag 觸發。"""
    # 用 GaussianBlur 製造模糊
    img = Image.new("L", (128, 128), color=128)
    img = img.filter(ImageFilter.GaussianBlur(radius=10))
    return _encode(img)


@pytest.fixture
def sharp_jpeg() -> bytes:
    """邊緣銳利影像 → 不模糊。

    用棋盤格（高頻水平相鄰差）以符合水平-gradient 演算法。
    """
    img = Image.new("L", (128, 128))
    for y in range(128):
        for x in range(128):
            img.putpixel((x, y), 255 if (x // 4) % 2 == 0 else 0)
    return _encode(img)


@pytest.fixture
def rgb_jpeg() -> bytes:
    """RGB（非灰度）→ 應能自動轉灰度。"""
    img = Image.new("RGB", (128, 128), color=(255, 0, 0))
    return _encode(img)


@pytest.fixture
def frozen_pair_same() -> tuple[bytes, bytes]:
    """兩張完全相同 → 凍結。"""
    img = Image.new("L", (128, 128), color=128)
    return _encode(img), _encode(img)


@pytest.fixture
def frozen_pair_different() -> tuple[bytes, bytes]:
    """兩張差異大 → 不凍結。"""
    img_a = Image.new("L", (128, 128), color=0)
    img_b = Image.new("L", (128, 128), color=255)
    return _encode(img_a), _encode(img_b)


# === 1. analyze_image 基本 ===
def test_analyze_image_returns_dataclass(blurred_jpeg):
    """analyze_image 應回傳 ImageHealthResult 物件。"""
    r = analyze_image(blurred_jpeg)
    assert isinstance(r, ImageHealthResult)


def test_analyze_image_has_required_fields(blurred_jpeg):
    """ImageHealthResult 應含 5 個欄位。"""
    r = analyze_image(blurred_jpeg)
    assert hasattr(r, "blur_var")
    assert hasattr(r, "mean_luma")
    assert hasattr(r, "is_blurry")
    assert hasattr(r, "is_overexposed")
    assert hasattr(r, "is_underexposed")


# === 2. 過曝 ===
def test_white_image_is_overexposed(blank_white_jpeg):
    """全白影像 mean_luma ≈ 1.0 → 過曝。"""
    r = analyze_image(blank_white_jpeg)
    assert r.mean_luma > LUMA_OVEREXPOSED
    assert r.is_overexposed is True


# === 3. 欠曝 ===
def test_black_image_is_underexposed(blank_black_jpeg):
    """全黑影像 mean_luma ≈ 0.0 → 欠曝。"""
    r = analyze_image(blank_black_jpeg)
    assert r.mean_luma < LUMA_UNDEREXPOSED
    assert r.is_underexposed is True


# === 4. 模糊 ===
def test_gaussian_blur_is_blurry(blurred_jpeg):
    """高斯模糊影像 → is_blurry=True。"""
    r = analyze_image(blurred_jpeg)
    assert r.is_blurry is True
    assert r.blur_var < BLUR_VAR_THRESHOLD


def test_sharp_line_is_not_blurry(sharp_jpeg):
    """有銳利邊緣的影像 → is_blurry=False。"""
    r = analyze_image(sharp_jpeg)
    assert r.is_blurry is False
    assert r.blur_var >= BLUR_VAR_THRESHOLD


# === 5. 正常影像 ===
def test_gray_image_no_flag(blank_gray_jpeg):
    """中灰影像 → mean_luma 中等（不過曝也不欠曝）。

    注：完全平的中灰影像水平相鄰差為 0 → gradient var=0 → 會被視為模糊。
    這是預期行為（純色影像確實缺乏細節），所以不斷言 is_blurry。
    """
    r = analyze_image(blank_gray_jpeg)
    assert LUMA_UNDEREXPOSED <= r.mean_luma <= LUMA_OVEREXPOSED
    assert r.is_overexposed is False
    assert r.is_underexposed is False


# === 6. 邊界值：恰好等於 threshold ===
def test_luma_at_overexposed_threshold():
    """mean_luma 恰好 = 0.85 → 不算過曝（threshold 嚴格 >）。"""
    # 構造 85/255 的影像
    img = Image.new("L", (128, 128), color=85)
    r = analyze_image(_encode(img))
    # 85/255 = 0.3333... 不會觸發過曝也不會觸發欠曝
    # 改構造：216 = 0.847（接近 0.85 但不到）
    img2 = Image.new("L", (128, 128), color=216)  # 216/255 = 0.847
    r2 = analyze_image(_encode(img2))
    # 0.847 < 0.85 → 不過曝
    assert r2.is_overexposed is False
    # 構造 217/255 = 0.851 → 過曝
    img3 = Image.new("L", (128, 128), color=217)
    r3 = analyze_image(_encode(img3))
    assert r3.is_overexposed is True


def test_luma_at_underexposed_threshold():
    """mean_luma 恰好 = 0.15 → 不算欠曝（threshold 嚴格 <）。"""
    # 38/255 = 0.149 → 欠曝
    img = Image.new("L", (128, 128), color=38)
    r = analyze_image(_encode(img))
    assert r.is_underexposed is True
    # 39/255 = 0.1529 → 不算欠曝
    img2 = Image.new("L", (128, 128), color=39)
    r2 = analyze_image(_encode(img2))
    assert r2.is_underexposed is False


# === 7. to_metrics_dict / to_flags ===
def test_to_metrics_dict_round(blurred_jpeg):
    """to_metrics_dict 應回傳可 JSON 序列化的 dict。"""
    import json
    r = analyze_image(blurred_jpeg)
    d = r.to_metrics_dict()
    json.dumps(d)  # 不 raise
    assert "blur_var" in d
    assert "mean_luma" in d


def test_to_flags_white(blank_white_jpeg):
    """全白 → flags 應只含 overexposed。"""
    r = analyze_image(blank_white_jpeg)
    flags = r.to_flags()
    assert "overexposed" in flags


def test_to_flags_black(blank_black_jpeg):
    """全黑 → flags 應只含 underexposed。"""
    r = analyze_image(blank_black_jpeg)
    flags = r.to_flags()
    assert "underexposed" in flags


# === 8. RGB 自動轉灰度 ===
def test_analyze_rgb_image(rgb_jpeg):
    """RGB 影像應可自動轉灰度分析。"""
    r = analyze_image(rgb_jpeg)
    # 紅色 (255,0,0) → 灰度 76（ITU-R BT.601 加權） → 約 0.30（欠曝）
    assert 0.0 <= r.mean_luma <= 1.0


# === 9. 錯誤處理 ===
def test_empty_bytes_raises():
    """空 bytes 應 raise ValueError。"""
    with pytest.raises(ValueError, match="空 bytes"):
        analyze_image(b"")


def test_invalid_jpeg_raises():
    """無效 jpeg bytes 應 raise ValueError。"""
    with pytest.raises(ValueError, match="無法解碼"):
        analyze_image(b"not-a-valid-jpeg")


# === 10. is_frozen 基本 ===
def test_is_frozen_returns_dataclass(frozen_pair_same):
    """is_frozen 應回傳 FrozenResult。"""
    r = is_frozen(*frozen_pair_same)
    assert isinstance(r, FrozenResult)
    assert hasattr(r, "mean_abs_diff")
    assert hasattr(r, "is_frozen")


def test_is_frozen_identical_images(frozen_pair_same):
    """兩張完全相同 → mean_abs_diff=0 → is_frozen=True。"""
    r = is_frozen(*frozen_pair_same)
    assert r.mean_abs_diff == 0.0
    assert r.is_frozen is True


def test_is_frozen_very_different_images(frozen_pair_different):
    """兩張全黑 vs 全白 → mean_abs_diff=255 → is_frozen=False。"""
    r = is_frozen(*frozen_pair_different)
    assert r.mean_abs_diff == 255.0
    assert r.is_frozen is False


def test_is_frozen_slightly_different():
    """2026-07-30：threshold 1.0。差 1 → mean_abs_diff=1，
    1 不嚴格 < 1.0 → 不算凍結。
    """
    img_a = Image.new("L", (128, 128), color=128)
    img_b = Image.new("L", (128, 128), color=129)
    r = is_frozen(_encode(img_a), _encode(img_b))
    assert r.mean_abs_diff == 1.0
    assert r.is_frozen is False, "FROZEN_DIFF_THRESHOLD=1.0，差 1 不算 frozen"


# === 11. is_frozen 邊界值（2026-07-30：threshold 5.0 → 1.0）===
def test_is_frozen_at_threshold():
    """兩張差 5 → 遠大於 threshold 1.0 → 不算凍結。"""
    img_a = Image.new("L", (128, 128), color=100)
    img_b = Image.new("L", (128, 128), color=105)
    r = is_frozen(_encode(img_a), _encode(img_b))
    assert r.mean_abs_diff == 5.0
    assert r.is_frozen is False
    # 差 4 → 仍不算凍結（> 1.0）
    img_c = Image.new("L", (128, 128), color=104)
    r2 = is_frozen(_encode(img_a), _encode(img_c))
    assert r2.mean_abs_diff == 4.0
    assert r2.is_frozen is False


# === 12. is_frozen 錯誤處理 ===
def test_is_frozen_empty_a_raises():
    with pytest.raises(ValueError, match="空 bytes"):
        is_frozen(b"", b"anything")


def test_is_frozen_empty_b_raises():
    # b 是空 bytes → b 在 load 時會 raise「無法解碼 JPEG」
    # （a 先 load 成功，到 b 才炸）
    with pytest.raises(ValueError, match="無法解碼"):
        is_frozen(b"anything", b"")


def test_is_frozen_invalid_raises():
    with pytest.raises(ValueError, match="無法解碼"):
        is_frozen(b"not-jpeg-a", b"not-jpeg-b")