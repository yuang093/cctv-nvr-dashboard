"""Week 5 #012-#016：5 個 feature flags 集中讀取。

預設全部關閉（Week 5 結束時 dashboard 行為 = Week 4），
Week 6 翻 flags 即可對外生效。

支援的旗標（皆預設 False）：
- NVR_AUTH_ENABLED：Flask-Login + IP 白名單 + 強制改密碼
- NVR_HTTPS_ENABLED：Werkzeug adhoc SSL
- NVR_RATE_LIMIT_ENABLED：flask-limiter per-IP 限流
- NVR_AUDIT_ENABLED：寫入 audit_log 表
- NVR_NVR_DOWNSCOPED：NVR 帳號 api_reader 權限自我驗證

白名單 / admin 密碼：
- NVR_OPS_TRUSTED_CIDRS（逗號分隔 CIDR；預設 127/8, 10/8, 172.16/12, 192.168/16）
- NVR_ADMIN_DEFAULT_PASSWORD（預設 'admin'）
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

_DEFAULT_OPS_CIDRS = (
    "127.0.0.0/8",       # loopback
    "10.0.0.0/8",        # private
    "172.16.0.0/12",     # private
    "192.168.0.0/16",    # private
)


def _env_bool(name: str, default: bool = False) -> bool:
    """讀 env var 為 bool。支援 1/true/yes/on 與 0/false/no/off（不分大小寫）。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUTHY:
        return True
    if val in _FALSY:
        return False
    raise ValueError(f"{name} 必須是 truthy/falsy 字串，得到：{raw!r}")


@dataclass(frozen=True)
class FeatureFlags:
    """5 個 feature flags + ops 白名單 + admin 預設密碼。"""

    auth_enabled: bool = False
    https_enabled: bool = False
    rate_limit_enabled: bool = False
    audit_enabled: bool = False
    nvr_downscoped: bool = False

    ops_trusted_cidrs: tuple[str, ...] = _DEFAULT_OPS_CIDRS
    admin_default_password: str = "admin"

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        """從環境變數建立 FeatureFlags（不可變實例）。"""
        cidrs_raw = os.getenv("NVR_OPS_TRUSTED_CIDRS")
        if cidrs_raw:
            cidrs = tuple(c.strip() for c in cidrs_raw.split(",") if c.strip())
        else:
            cidrs = _DEFAULT_OPS_CIDRS
        return cls(
            auth_enabled=_env_bool("NVR_AUTH_ENABLED"),
            https_enabled=_env_bool("NVR_HTTPS_ENABLED"),
            rate_limit_enabled=_env_bool("NVR_RATE_LIMIT_ENABLED"),
            audit_enabled=_env_bool("NVR_AUDIT_ENABLED"),
            nvr_downscoped=_env_bool("NVR_NVR_DOWNSCOPED"),
            ops_trusted_cidrs=cidrs,
            admin_default_password=os.getenv("NVR_ADMIN_DEFAULT_PASSWORD", "admin"),
        )
