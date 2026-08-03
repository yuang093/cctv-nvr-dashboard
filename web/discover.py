"""
web/discover.py
================
Phase 2.8（Arisan）Phase #6：CIDR 探索網段工具。

設計重點：
- CIDR 展開使用標準庫 `ipaddress`（避免引入第三方）
- prefix < /16 一律拒絕（防 DoS，255×255×255×255 = 4B IPs）
- 探測用 ThreadPoolExecutor(8 workers) + requests.get(verify=False)
- Server header 含 "Avigilon" 字串視為 Avigilon NVR
- 任何例外（含 timeout / connection refused / SSL error）都 swallow，
  記錄 error=class_name（probe 永遠不 raise，避免整批卡住）
- 既有 NVR IP 用 `skipped=True` 標記，不 probe
"""
from __future__ import annotations

import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_PROBE_TIMEOUT = 3.0
DEFAULT_MAX_WORKERS = 8
MIN_PREFIX_LEN = 16  # 拒絕 /16 以下（防 DoS；/16 = 65536 IPs）

_AVIGILON_MARKER = "Avigilon"


def expand_cidr(cidr: str, *, min_prefix_len: int = MIN_PREFIX_LEN) -> list[str]:
    """展開 CIDR 為所有 host IP 字串 list。

    Args:
        cidr: 例如 "192.168.0.0/24"。
        min_prefix_len: 最低 prefix length（預設 16）；小於此值拒絕。

    Returns:
        排序過的 IP 字串 list（已排除 network / broadcast address）。

    Raises:
        ValueError: CIDR 不合法或 prefix < min_prefix_len。
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise ValueError(f"CIDR 不合法：{cidr}（{e}）") from e
    if net.prefixlen < min_prefix_len:
        raise ValueError(
            f"CIDR 範圍過大（/{net.prefixlen} = {net.num_addresses} IPs），"
            f"必須 ≥ /{min_prefix_len}（{2 ** (32 - min_prefix_len)} IPs）"
            f"以防 DoS。"
        )
    return [str(h) for h in net.hosts()]


def probe_ip(ip: str, port: int = 8443, timeout: float = DEFAULT_PROBE_TIMEOUT) -> dict:
    """對單一 IP 做 Avigilon 探測。

    回傳 dict schema：
        {
          "ip": str,
          "open": bool,         # 有回應（任何 status code）
          "is_avigilon": bool,  # Server header / X-Powered-By 含 "Avigilon"
          "status_code": int | None,
          "error": str | None,  # 例外 class name 或 None
        }

    任何例外（含 timeout / connection refused / SSL error）都 swallow，
    回 error=class_name + open=False，**絕不 raise**。
    """
    url = f"https://{ip}:{port}/"
    try:
        resp = requests.get(url, timeout=timeout, verify=False)
        server = (resp.headers.get("Server") or "")
        powered_by = (resp.headers.get("X-Powered-By") or "")
        is_avigilon = _AVIGILON_MARKER in server or _AVIGILON_MARKER in powered_by
        return {
            "ip": ip,
            "open": True,
            "is_avigilon": is_avigilon,
            "status_code": resp.status_code,
            "error": None,
        }
    except Exception as e:
        return {
            "ip": ip,
            "open": False,
            "is_avigilon": False,
            "status_code": None,
            "error": type(e).__name__,
        }


def run_discovery(
    *,
    cidr: str,
    port: int = 8443,
    skip_ips: Iterable[str] = (),
    workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> list[dict]:
    """展開 CIDR 並平行 probe，回傳按原始 CIDR 順序的 result list。

    Args:
        cidr: 目標 CIDR。
        port: 探測 port（預設 8443）。
        skip_ips: 不 probe 的 IP set（既有 NVR）。
        workers: ThreadPoolExecutor max_workers。
        timeout: 單 IP probe timeout（秒）。

    Returns:
        list of result dict，順序與 expand_cidr 結果一致。
        skip_ips 內的 IP 會以 `{"ip": ..., "skipped": True, "reason": "existing_nvr"}` 表示。

    Raises:
        ValueError: CIDR 不合法（從 expand_cidr 傳出）。
    """
    ips = expand_cidr(cidr)
    skip_set = set(skip_ips)
    to_probe = [ip for ip in ips if ip not in skip_set]

    # Probe 結果以 ip 為 key
    probed: dict[str, dict] = {}
    if to_probe:
        with ThreadPoolExecutor(max_workers=min(workers, len(to_probe))) as ex:
            futures = {ex.submit(probe_ip, ip, port, timeout): ip for ip in to_probe}
            for fut in as_completed(futures):
                ip = futures[fut]
                probed[ip] = fut.result()

    # 按原始 CIDR 順序輸出（包含 skipped）
    out: list[dict] = []
    for ip in ips:
        if ip in skip_set:
            out.append({
                "ip": ip,
                "skipped": True,
                "reason": "existing_nvr",
                "open": False,
                "is_avigilon": False,
                "status_code": None,
                "error": None,
            })
        else:
            out.append(probed.get(ip, {
                "ip": ip,
                "skipped": False,
                "reason": None,
                "open": False,
                "is_avigilon": False,
                "status_code": None,
                "error": "no_result",
            }))
    return out


def run_discovery_for_session(
    db_path: str,
    session_id: int,
    *,
    workers: int = DEFAULT_MAX_WORKERS,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> None:
    """對既有 discover_sessions row 執行 probe 並回填結果。

    從 DB 讀 session.cidr/port → 取得既有 NVR IPs → run_discovery →
    update_discover_session 寫回結果 + status='completed'。

    任何例外都會把 status 設為 'failed'（避免 session 卡在 running）。
    """
    # 避免循環 import
    from web import db as webdb

    sess = webdb.get_discover_session(db_path, session_id)
    if not sess:
        return

    # 標記 running（給 UI poll 用）
    webdb.update_discover_session(
        db_path, session_id, results=[], status="running",
    )

    try:
        existing_ips = webdb.get_existing_nvr_ips(db_path)
        port = webdb.get_session_port(db_path, session_id)
        results = run_discovery(
            cidr=sess["cidr"],
            port=port,
            skip_ips=existing_ips,
            workers=workers,
            timeout=timeout,
        )
        webdb.update_discover_session(
            db_path, session_id, results=results, status="completed",
        )
    except Exception:
        webdb.update_discover_session(
            db_path, session_id, results=[], status="failed",
        )
