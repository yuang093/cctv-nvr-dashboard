"""Week 5 #012：D2 內網 IP 白名單判斷。

使用標準函式庫 ipaddress，零外部依賴。
"""
from __future__ import annotations

import ipaddress


def is_trusted_ip(ip: str, trusted_cidrs: tuple[str, ...]) -> bool:
    """檢查 IP 是否落在任何 trusted CIDR 內。

    Args:
        ip: 要檢查的 IP（字串）
        trusted_cidrs: CIDR 列表（例如 ("127.0.0.0/8", "192.168.0.0/16")）

    Returns:
        True if IP 落在任一 CIDR 內；False otherwise（含 invalid IP）
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in trusted_cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            # 無效 CIDR 跳過（不應崩潰）
            continue
    return False
