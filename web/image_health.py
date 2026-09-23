"""
web/image_health.py
===================
Phase 2.8（Arisan 影像健康巡檢）：純函式影像分析。

4 種自製偵測：
  - 模糊（blur）：Laplacian variance approximation
  - 過曝（overexposed）：mean luminance > 0.85
  - 欠曝（underexposed）：mean luminance < 0.15
  - 凍結（frozen）：兩張 jpeg 的 mean abs pixel diff < 5

不引 OpenCV，僅 Pillow + stdlib。
門檻值先用常數啟動（見下方 _THRESHOLDS），後續可從 nvr_config.json 讀。

與既有 DEVICE_TAMPERING（遮擋/位移）的分工：
  - 遮擋/位移 → NVR ACC 內建 analytics 自動偵測 → DEVICE_TAMPERING event
  - 模糊/過曝/欠曝/凍結 → 自製影像分析 → image_health_checks 表
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, cast

from PIL import Image


# === Threshold 常數（可後續從 config 讀，先用常數啟動） ===
# 注：gradient-var 演算法對水平相鄰像素敏感（不計垂直），故實測正常影像 >= 100，
# blur 影像接近 0；100 是保守門檻避免誤報。
BLUR_VAR_THRESHOLD = 100.0  # gradient var < 100 → 模糊
LUMA_OVEREXPOSED = 0.85  # mean luma > 0.85 → 過曝
LUMA_UNDEREXPOSED = 0.15  # mean luma < 0.15 → 欠曝
# 2026-07-30：5.0 太寬鬆（正常 cam 變化 1~4 也會被判 frozen）。
# 改 1.0：只有「真的幾乎沒變」才算 frozen。
FROZEN_DIFF_THRESHOLD = 1.0  # 兩張 mean abs pixel diff < 1.0 → 凍結
# 2026-07-30：曾嘗試拉高到 5.0 救 cam B 明亮靜態場景，
# 但實測驗證 5.0 反而更寬鬆（diff 0.24 < 5.0 仍 frozen），
# 對誤報無解。保留 1.0，搭配 dark_scene_skip 與
# 「healthy cam 不觸發 event」shadow 模式另議。
# 2026-07-30：過暗場景跳過 frozen 判定。
# 夜視模式拍空曠場景，5 秒內完全沒變化是常態，不該誤報 frozen。
# cam2 (物料暫存區-2) mean_luma 0.29 + frozen_diff 0.2 就是這個情境。
LUMA_DARK_SKIP_FROZEN = 0.30  # mean_luma < 0.30 → 跳過 frozen 判定

# 為效能，分析時縮成這個尺寸（足夠判斷模糊/曝光/凍結）
_ANALYZE_SIZE = (64, 64)


@dataclass
class ImageHealthResult:
    """單張影像分析結果（單張 metric）。"""

    blur_var: float  # Laplacian variance，越高越銳利
    mean_luma: float  # 0~1 灰度均值
    is_blurry: bool
    is_overexposed: bool
    is_underexposed: bool

    def to_metrics_dict(self) -> dict[str, Any]:
        """轉成 metrics_json 內的 dict（給 image_health_checks.metrics_json）。"""
        return {
            "blur_var": round(self.blur_var, 3),
            "mean_luma": round(self.mean_luma, 4),
            "is_blurry": self.is_blurry,
            "is_overexposed": self.is_overexposed,
            "is_underexposed": self.is_underexposed,
        }

    def to_flags(self) -> list[str]:
        """回傳被觸發的 flag 名稱（給 flags_json）。"""
        flags: list[str] = []
        if self.is_blurry:
            flags.append("blurry")
        if self.is_overexposed:
            flags.append("overexposed")
        if self.is_underexposed:
            flags.append("underexposed")
        return flags


@dataclass
class FrozenResult:
    """兩張影像凍結比對結果。"""

    mean_abs_diff: float  # mean abs pixel diff（0~255）
    is_frozen: bool


# === Pillow / input 處理 ===
def _load_grayscale(
    jpeg_bytes: bytes, size: tuple[int, int] = _ANALYZE_SIZE
) -> Image.Image:
    """解碼 jpeg bytes 並縮成灰度影像。

    Raises:
        ValueError: bytes 為空或無法解碼
    """
    if not jpeg_bytes:
        raise ValueError("空 bytes，無法解碼影像")
    try:
        img = cast(Image.Image, Image.open(BytesIO(jpeg_bytes)))
        img.load()  # 強制 decode（BytesIO close 後仍可讀）
    except Exception as e:
        raise ValueError(f"無法解碼 JPEG：{e}") from e
    img = cast(Image.Image, img.convert("L"))  # 灰度
    if size is not None:
        # Pillow 9.1+ 把 BILINEAR 等常數搬到 Image.Resampling；舊版直接掛在 Image
        resample = (
            Image.Resampling.BILINEAR
            if hasattr(Image, "Resampling")
            else getattr(Image, "BILINEAR", 1)
        )
        # Pillow 回傳 Image.Image 型別；cast 給 mypy 看
        img = cast(Image.Image, img.resize(size, resample))
    return img


# === 單張 metric ===
def _laplacian_var_approx(img: Image.Image) -> float:
    """Gradient variance approximation（無 OpenCV，numpy-free）。

    演算法：
      1. 對灰度影像算「水平相鄰像素 abs diff」梯度
      2. 算梯度值的 variance

    為何這樣近似：Laplacian var ≈ gradient var（皆二階微分能量）。
    blur 影像相鄰像素幾乎相等 → 梯度小 → var 低。
    銳利影像邊緣多 → 梯度大 → var 高。

    用 tobytes() 拿原始 bytes（避免 Pillow 12 getdata() deprecation），
    一次 O(W*H) pass，超快。
    """
    raw = img.tobytes()  # 長度 = W*H，每 byte 一個 pixel
    n = len(raw)
    if n < 2:
        return 0.0
    # 算相鄰 diff 的 sum / sum_sq（O(n) 一次走訪）
    s = 0
    s2 = 0
    for i in range(n - 1):
        d = raw[i + 1] - raw[i]
        if d < 0:
            d = -d
        s += d
        s2 += d * d
    mean = s / (n - 1)
    var = (s2 / (n - 1)) - mean * mean
    return float(var)


def _mean_luma(img: Image.Image) -> float:
    """灰度影像的 mean luminance（正規化到 0~1）。"""
    hist = img.histogram()  # 0~255
    n = sum(hist)
    if n == 0:
        return 0.0
    return sum(i * c for i, c in enumerate(hist)) / (n * 255.0)


def analyze_image(jpeg_bytes: bytes) -> ImageHealthResult:
    """分析單張 jpeg，回傳 ImageHealthResult。

    Args:
        jpeg_bytes: JPEG 編碼的影像 bytes

    Returns:
        ImageHealthResult，含 blur_var / mean_luma 與 3 個 bool flag
        （blurry / overexposed / underexposed；frozen 需另外呼叫 is_frozen）

    Raises:
        ValueError: bytes 為空或無法解碼
    """
    img = _load_grayscale(jpeg_bytes)
    blur_var = _laplacian_var_approx(img)
    mean_luma = _mean_luma(img)
    return ImageHealthResult(
        blur_var=blur_var,
        mean_luma=mean_luma,
        is_blurry=blur_var < BLUR_VAR_THRESHOLD,
        is_overexposed=mean_luma > LUMA_OVEREXPOSED,
        is_underexposed=mean_luma < LUMA_UNDEREXPOSED,
    )


# === 兩張 metric：凍結偵測 ===
def is_frozen(jpeg_a: bytes, jpeg_b: bytes) -> FrozenResult:
    """比對兩張 jpeg 是否「凍結」（mean abs pixel diff）。

    2026-07-30：過暗場景（mean_luma < LUMA_DARK_SKIP_FROZEN）跳過判定，
    避免夜視模式拍空曠場景誤報（cam2 真實情境：luma 0.29、diff 0.2）。

    Args:
        jpeg_a: 第一張 jpeg bytes
        jpeg_b: 第二張 jpeg bytes（與 a 間隔 5s 抓）

    Returns:
        FrozenResult，含 mean_abs_diff 與 is_frozen bool

    Raises:
        ValueError: 任一 bytes 為空或無法解碼
    """
    img_a = _load_grayscale(jpeg_a)
    img_b = _load_grayscale(jpeg_b)
    # 兩張都用相同 _ANALYZE_SIZE → 同一尺寸可比
    # Pillow 12 起 getdata() deprecated，用 tobytes() 拿原始 bytes（更快 + 相容）
    a = img_a.tobytes()
    b = img_b.tobytes()
    n = min(len(a), len(b))
    if n == 0:
        raise ValueError("影像資料為空")
    diff = sum(abs(a[i] - b[i]) for i in range(n)) / n
    # 過暗場景跳過（夜視模式拍空曠場景的常態變化小）
    luma_a = _mean_luma(img_a)
    if luma_a < LUMA_DARK_SKIP_FROZEN:
        return FrozenResult(
            mean_abs_diff=float(diff),
            is_frozen=False,  # 過暗場景不算 frozen
        )
    return FrozenResult(
        mean_abs_diff=float(diff),
        is_frozen=diff < FROZEN_DIFF_THRESHOLD,
    )
