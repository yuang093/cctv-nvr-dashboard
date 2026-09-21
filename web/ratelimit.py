"""Week 5 #014：flask-limiter 整合。

Feature flag：NVR_RATE_LIMIT_ENABLED=False → 完全不安裝 limiter。
啟用後：
- /login、/query、POST /devices/* → per-IP 限流（10 req/min）
- 內網 IP（白名單）→ 透過 exempt_when 直接跳過限流

實作要點：用 limiter.limit(..., exempt_when=...) 對每個裝飾的 endpoint
套用內網 IP 豁免；key_func 直接用 IP。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask
from flask_limiter import util as _flask_limiter_util

from web.auth.ip_whitelist import is_trusted_ip
from web.config import FeatureFlags

if TYPE_CHECKING:
    from flask_limiter import Limiter


def make_exempt_when_trusted_ip():
    """回傳一個 closure：判斷目前 request 的 remote IP 是否為內網白名單。

    給 flask-limiter 的 exempt_when 參數使用。
    """

    def _exempt_when() -> bool:
        from flask import current_app

        ip = _flask_limiter_util.get_remote_address() or "0.0.0.0"
        flags: FeatureFlags = current_app.config["FEATURE_FLAGS"]
        return is_trusted_ip(ip, trusted_cidrs=flags.ops_trusted_cidrs)

    return _exempt_when


def install_rate_limiter(app: Flask) -> None:
    """註冊 flask-limiter 到 app。Feature flag 關閉時完全不安裝。

    Args:
        app: Flask application instance
    """
    flags: FeatureFlags = app.config["FEATURE_FLAGS"]

    # Feature flag 關閉 → 根本不安裝
    if not flags.rate_limit_enabled:
        return

    from flask_limiter import Limiter  # 延遲 import（避免 flag 關閉時的依賴）

    storage_uri = app.config.get("NVR_RATE_LIMIT_STORAGE_URI", "memory://")

    def _key_func() -> str:
        """Per-IP 限流。"""
        return _flask_limiter_util.get_remote_address() or "0.0.0.0"

    limiter = Limiter(
        key_func=_key_func,
        app=app,
        storage_uri=storage_uri,
        default_limits=[],  # 不預設，全靠裝飾器
    )

    # 把 limiter 存到 extensions 供其他模組用
    app.extensions["nvr_limiter"] = limiter
    app.extensions["nvr_limit_exempt_when"] = make_exempt_when_trusted_ip()
