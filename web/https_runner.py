"""Week 5 #013：Werkzeug adhoc SSL context 產生器。

mode:
- 'none' → None（HTTP，預設）
- 'adhoc' → Werkzeug adhoc 自簽憑證（dev 用）
- 'cert' → 讀 PEM 檔（prod reverse proxy 後方用）
"""
from __future__ import annotations

import ssl
from pathlib import Path
from typing import Optional


def build_ssl_context(
    mode: str = "none",
    cert_file: Optional[str] = None,
    key_file: Optional[str] = None,
) -> Optional[ssl.SSLContext]:
    """依 mode 產生 ssl_context 或 None。

    Args:
        mode: 'none' / 'adhoc' / 'cert'
        cert_file: PEM 憑證路徑（mode='cert' 才需要）
        key_file: PEM key 路徑（mode='cert' 才需要）

    Returns:
        ssl.SSLContext / "adhoc" 字串 / None
        - 'none' → None（HTTP）
        - 'adhoc' → "adhoc" 字串（Werkzeug 接受，會在執行時自簽憑證）
        - 'cert' → 真實 SSLContext

    Raises:
        ValueError: 未知 mode
        FileNotFoundError: cert_file 或 key_file 不存在（mode='cert'）
    """
    if mode == "none":
        return None
    if mode == "adhoc":
        # Werkzeug 內建 adhoc：執行時動態產生 self-signed cert
        return "adhoc"  # type: ignore[return-value]
    if mode == "cert":
        if not cert_file or not key_file:
            raise ValueError("mode='cert' 需要 cert_file + key_file")
        if not Path(cert_file).exists():
            raise FileNotFoundError(f"cert_file 不存在：{cert_file}")
        if not Path(key_file).exists():
            raise FileNotFoundError(f"key_file 不存在：{key_file}")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_file, key_file)
        return ctx
    raise ValueError(f"未知 mode：{mode!r}")
