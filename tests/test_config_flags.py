"""Week 5：FeatureFlags 集中模組測試（Task 1）。"""
from __future__ import annotations

import pytest

from web.config import FeatureFlags


def test_defaults_all_false_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有 flag 預設 False（env 全未設）。"""
    for name in (
        "NVR_AUTH_ENABLED",
        "NVR_HTTPS_ENABLED",
        "NVR_RATE_LIMIT_ENABLED",
        "NVR_AUDIT_ENABLED",
        "NVR_NVR_DOWNSCOPED",
    ):
        monkeypatch.delenv(name, raising=False)
    flags = FeatureFlags.from_env()
    assert flags.auth_enabled is False
    assert flags.https_enabled is False
    assert flags.rate_limit_enabled is False
    assert flags.audit_enabled is False
    assert flags.nvr_downscoped is False


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """env var = '1' → flag = True。"""
    monkeypatch.setenv("NVR_AUTH_ENABLED", "1")
    assert FeatureFlags.from_env().auth_enabled is True


def test_env_var_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """支援 '1' / 'true' / 'yes' / 'on'（不分大小寫）。"""
    monkeypatch.setenv("NVR_AUTH_ENABLED", "TRUE")
    assert FeatureFlags.from_env().auth_enabled is True
    monkeypatch.setenv("NVR_AUTH_ENABLED", "yes")
    assert FeatureFlags.from_env().auth_enabled is True
    monkeypatch.setenv("NVR_AUTH_ENABLED", "On")
    assert FeatureFlags.from_env().auth_enabled is True


def test_env_var_falsy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """支援 '0' / 'false' / 'no' / 'off'（不分大小寫）。"""
    monkeypatch.setenv("NVR_AUTH_ENABLED", "1")
    assert FeatureFlags.from_env().auth_enabled is True
    monkeypatch.setenv("NVR_AUTH_ENABLED", "0")
    assert FeatureFlags.from_env().auth_enabled is False
    monkeypatch.setenv("NVR_AUTH_ENABLED", "FALSE")
    assert FeatureFlags.from_env().auth_enabled is False


def test_invalid_env_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """未知的 env 值應 raise ValueError（避免靜默錯誤）。"""
    monkeypatch.setenv("NVR_AUTH_ENABLED", "maybe")
    with pytest.raises(ValueError, match="NVR_AUTH_ENABLED"):
        FeatureFlags.from_env()


def test_ops_trusted_cidrs_default() -> None:
    """D2：預設信任 4 段內網 CIDR。"""
    flags = FeatureFlags.from_env()
    assert "127.0.0.0/8" in flags.ops_trusted_cidrs
    assert "10.0.0.0/8" in flags.ops_trusted_cidrs
    assert "172.16.0.0/12" in flags.ops_trusted_cidrs
    assert "192.168.0.0/16" in flags.ops_trusted_cidrs


def test_ops_trusted_cidrs_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """NVR_OPS_TRUSTED_CIDRS 可覆寫預設白名單（comma-separated）。"""
    monkeypatch.setenv("NVR_OPS_TRUSTED_CIDRS", "192.168.1.0/24,10.0.0.0/8")
    flags = FeatureFlags.from_env()
    assert "192.168.1.0/24" in flags.ops_trusted_cidrs
    assert "10.0.0.0/8" in flags.ops_trusted_cidrs
    # 預設被覆寫
    assert "192.168.0.0/16" not in flags.ops_trusted_cidrs


def test_admin_default_password_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2：admin 預設密碼從 .env 讀，若無則 'admin'。"""
    monkeypatch.setenv("NVR_ADMIN_DEFAULT_PASSWORD", "MySecure!Pass1")
    assert FeatureFlags.from_env().admin_default_password == "MySecure!Pass1"
    monkeypatch.delenv("NVR_ADMIN_DEFAULT_PASSWORD", raising=False)
    assert FeatureFlags.from_env().admin_default_password == "admin"


def test_feature_flags_is_frozen() -> None:
    """FeatureFlags 是 frozen dataclass，建立後不可改。"""
    flags = FeatureFlags.from_env()
    with pytest.raises(Exception):  # FrozenInstanceError
        flags.auth_enabled = True  # type: ignore[misc]
