"""Week 5 #013：Werkzeug adhoc SSL + reverse proxy 文件測試（Task 8）。"""
from __future__ import annotations

import ssl

import pytest

from web.https_runner import build_ssl_context


def test_build_ssl_context_adhoc_returns_placeholder() -> None:
    """mode='adhoc' → 回傳 'adhoc' 字串（Werkzeug 會在執行時自簽）。"""
    ctx = build_ssl_context(mode="adhoc")
    assert ctx == "adhoc"


def test_build_ssl_context_none_returns_none() -> None:
    """mode='none' → 回傳 None（HTTP，預設）。"""
    assert build_ssl_context(mode="none") is None


def test_build_ssl_context_cert_loads_pem(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """mode='cert' → 載入 PEM 檔回傳 SSLContext。"""
    # 用 self-signed cert：openssl 在 Windows 不一定可用 → 改用 werkzeug 產 adhoc
    # 並 export 為 PEM
    import datetime

    # 生成 self-signed cert 用 cryptography（werkzeug 內建會用）
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "test.local"),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        ctx = build_ssl_context(
            mode="cert", cert_file=str(cert_path), key_file=str(key_path)
        )
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is False
    except ImportError:
        pytest.skip("cryptography 套件未安裝，跳過此測試")


def test_build_ssl_context_cert_missing_files_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """mode='cert' 但檔案不存在 → FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        build_ssl_context(
            mode="cert",
            cert_file=str(tmp_path / "missing.pem"),
            key_file=str(tmp_path / "missing.key"),
        )


def test_build_ssl_context_unknown_mode_raises() -> None:
    """未知 mode → ValueError。"""
    with pytest.raises(ValueError, match="未知 mode"):
        build_ssl_context(mode="unknown")


def test_build_ssl_context_cert_requires_both_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """mode='cert' 必須同時給 cert_file + key_file。"""
    with pytest.raises(ValueError, match="cert_file"):
        build_ssl_context(mode="cert", cert_file=None, key_file=None)
