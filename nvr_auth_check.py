"""Week 5 #016：NVR 端帳號權限自我驗證。

登入後呼叫 /server/users/current 或類似端點（依 ACC API）檢查目前 session 的
帳號是否具備『唯讀』角色（api_reader 或 View 角色）。

回傳 (ok, message)：
- ok=True → 帳號有唯讀角色，可繼續掃描
- ok=False → 帳號權限過大或驗證失敗，建議改用 api_reader
"""
from __future__ import annotations

import urllib3
from typing import Tuple

import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def check_nvr_permissions(host: str, session_token: str, timeout: int = 10) -> Tuple[bool, str]:
    """檢查 NVR session 的帳號是否具備 api_reader 唯讀角色。

    Args:
        host: NVR host（不含 protocol/port；會自動加 https://:8443）
        session_token: 登入後取得的 session token（Cookie 或 Authorization header）
        timeout: HTTP timeout（秒）

    Returns:
        (ok, message) tuple：
        - ok=True → 帳號具備唯讀角色，可繼續掃描
        - ok=False → 帳號權限過大或驗證失敗，建議改用 api_reader
    """
    # ACC 8.x 的「目前使用者」端點：/api/rest/v1/users/current
    url = f"https://{host}:8443/api/rest/v1/users/current"
    session = requests.Session()
    try:
        resp = session.get(
            url,
            headers={
                "Accept": "application/json",
                "Cookie": f"session={session_token}",
            },
            verify=False,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        # ACC 用戶物件含 roles 陣列（每個 role 有 name 欄位）
        roles_raw = data.get("roles", [])
        roles = {r.get("name", "") for r in roles_raw}
        if "api_reader" in roles or "View" in roles or "Viewer" in roles:
            return True, (
                f"OK：帳號具備唯讀角色（{', '.join(sorted(r for r in roles if r))}）"
            )
        return False, (
            f"FAIL：帳號角色 = {sorted(roles)}，缺少 'api_reader'。"
            "請在 NVR 端建立 api_reader 群組並將此帳號加入。"
        )
    except requests.RequestException as e:
        return False, f"FAIL：無法連線或 API 錯誤：{e}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法：python nvr_auth_check.py <host> <session_token>", file=sys.stderr)
        sys.exit(2)
    ok, msg = check_nvr_permissions(sys.argv[1], sys.argv[2])
    print(msg)
    sys.exit(0 if ok else 1)
