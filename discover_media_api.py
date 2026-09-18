"""
discover_media_api.py
=====================
步驟 0（Phase 2.7 影片片段探勘）：摸出 NVR Media API 端點形狀。

跟 discover_event_subtopics.py 一樣的探勘流程，但目標是 port 8555：
1. 用 /mt/api/rest/v1/login 登入拿 session token
2. 對 port 8555 列常見 Media API 路徑（/media/, /streaming/, /video/, /playback/, /recording/...）
3. 用該 session token 作 query param 試 GET
4. 對每條路徑印：HTTP status、Content-Type、前 60 bytes

設計目的：原廠文件 WebFetch 抓不到、找不到技術內容，
唯一能判斷「這 NVR 怎麼吐影片」的辦法就是直接打。

執行：
    python discover_media_api.py

環境需求（.env 或 export）：
    AVIGILON_USER_NONCE / AVIGILON_USER_KEY  [必]
    AVIGILON_USERNAME / AVIGILON_PASSWORD    [選，覆寫 nvr_config.json]

輸出：
    終端機表格 + Markdown 報告（建議存檔備查）
"""

from __future__ import annotations

import json
import os
import sys
import urllib3
from pathlib import Path

import requests

from nvr_scanner import (
    AvigilonScanner,
    ScannerError,
    load_env_file,
)


# === 自訂例外 ===
class ProbeError(Exception):
    """探勘過程中所有錯誤的包裝。"""


# === 設定 ===
CONFIG_FILENAME = "nvr_config.json"
LOGIN_TIMEOUT = 10  # 8443 登入逾時
PROBE_TIMEOUT = 5  # 8555 探測逾時
PROBE_BODY_PEEK = 80  # 每條 response 看前幾 bytes

# 可能路徑（按經驗 + 常見 NVR 命名）
CANDIDATE_PATHS = [
    # Avigilon Web Endpoint 命名空間（最可能）
    "/mt/api/rest/v1/media/stream",
    "/mt/api/rest/v1/media/recording",
    "/mt/api/rest/v1/media/playback",
    "/mt/api/rest/v1/streams/cameras",
    # 直接 port 8555 上的服務
    "/media/stream",
    "/media/playback",
    "/media/recording",
    "/streaming/cameras",
    "/streaming/playback",
    "/video/playback",
    "/recording/playback",
    "/playback",
    "/api/v1/media",
    "/api/v1/playback",
    "/api/media",
    "/",
    "/health",
    "/healthz",
]

# query 範本（用一個假相機 id — 真實 deviceId 之後從 cameras 表拿）
PROBE_QUERY_TEMPLATE = {
    "session": "<LOGIN-TOKEN>",
    "cameraId": "test-camera-id-placeholder",
    "startTime": "2026-07-06T00:00:00Z",
    "endTime": "2026-07-06T00:00:30Z",
}


def load_first_nvr(config_path: Path) -> dict:
    """讀 nvr_config.json 第一台 NVR（同 discover_event_subtopics）。"""
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as exc:
        raise ProbeError(f"找不到設定檔：{config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ProbeError(f"設定檔 JSON 格式錯誤：{exc}") from exc
    servers = config.get("nvr_servers", [])
    if not servers:
        raise ProbeError("設定檔中沒有任何 nvr_servers 項目")
    return servers[0]


def safe_peek(body: bytes | str | None) -> str:
    """取前 N bytes 並做 safe 顯示（binary 轉 repr）。"""
    if not body:
        return "(empty)"
    if isinstance(body, bytes):
        try:
            txt = body[:PROBE_BODY_PEEK].decode("utf-8")
        except UnicodeDecodeError:
            return repr(body[:PROBE_BODY_PEEK])
    else:
        txt = body[:PROBE_BODY_PEEK]
    return txt.replace("\n", "\\n").replace("\r", "\\r")


def probe_path(
    session: requests.Session,
    base_url: str,
    path: str,
    timeout: int,
) -> dict:
    """對單一路徑 GET，回傳診斷 dict。"""
    params = dict(PROBE_QUERY_TEMPLATE)  # copy
    url = f"{base_url}{path}"
    try:
        resp = session.get(
            url,
            params=params,
            timeout=timeout,
            verify=False,
            allow_redirects=False,
        )
        return {
            "path": path,
            "status": resp.status_code,
            "content_type": resp.headers.get("Content-Type", "(none)"),
            "content_length": resp.headers.get("Content-Length", "?"),
            "peek": safe_peek(resp.content),
        }
    except requests.exceptions.Timeout:
        return {
            "path": path,
            "status": "TIMEOUT",
            "content_type": "-",
            "content_length": "-",
            "peek": "-",
        }
    except requests.exceptions.ConnectionError as exc:
        return {
            "path": path,
            "status": "CONN_ERR",
            "content_type": "-",
            "content_length": "-",
            "peek": str(exc)[:60],
        }
    except Exception as exc:
        return {
            "path": path,
            "status": "ERR",
            "content_type": type(exc).__name__,
            "content_length": "-",
            "peek": str(exc)[:60],
        }


def print_table(nvr: dict, results: list[dict]) -> None:
    """終端機表格輸出。"""
    print()
    print("=" * 80)
    print(f"NVR Media API 探勘：{nvr.get('name', '?')} @ {nvr['host']}:8555")
    print("=" * 80)
    print(f"{'Path':<42} {'Status':>10}  {'Content-Type':<28} {'Peek'}")
    print("-" * 80)
    for r in results:
        ct = r["content_type"][:28] if r["content_type"] else "-"
        peek = r["peek"][:40].replace("|", "\\|")
        status = str(r["status"])[:10]
        print(f"{r['path']:<42} {status:>10}  {ct:<28} {peek}")
    print()


def save_markdown(nvr: dict, results: list[dict], out_path: Path) -> None:
    """存成 Markdown 報告（給會議 / issue 引用）。"""
    lines = [
        f"# NVR Media API 探勘報告：{nvr.get('name', '?')}",
        "",
        f"- 主機：`{nvr['host']}`",
        "- Port：8443（REST）/ **8555**（Media）",
        f"- 探測時間：{Path(__file__).stat().st_mtime}",  # placeholder
        "",
        "| Path | Status | Content-Type | Peek |",
        "|------|--------|--------------|------|",
    ]
    for r in results:
        ct = r["content_type"].replace("|", "\\|")
        peek = r["peek"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{r['path']}` | {r['status']} | {ct} | {peek} |")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  報告儲存：{out_path}")


def main() -> int:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    base_dir = Path(__file__).parent
    loaded = load_env_file(base_dir / ".env")
    if loaded:
        print(f"[INFO] 已從 .env 載入 {loaded} 個環境變數")

    try:
        nvr = load_first_nvr(base_dir / CONFIG_FILENAME)
    except ProbeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    if not user_nonce or not user_key:
        print("[FAIL] 需要 AVIGILON_USER_NONCE / AVIGILON_USER_KEY", file=sys.stderr)
        return 1

    # 套用帳密 env 覆寫
    nvr = dict(nvr)
    nvr["username"] = os.environ.get("AVIGILON_USERNAME") or nvr["username"]
    nvr["password"] = os.environ.get("AVIGILON_PASSWORD") or nvr["password"]

    # === 1. 登入拿 session token ===
    print(f"[INFO] 登入 {nvr['host']}:8443 ...")
    try:
        scanner = AvigilonScanner(
            nvr,
            user_nonce=user_nonce,
            user_key=user_key,
            integration_id=os.environ.get("AVIGILON_INTEGRATION_ID", ""),
            timeout=LOGIN_TIMEOUT,
        )
        scanner.login()
        session_token = scanner._session_token
        print(f"[OK]   拿到 session token（前 12 字）：{session_token[:12]}...")
    except ScannerError as exc:
        print(f"[FAIL] 登入失敗：{exc}", file=sys.stderr)
        return 1

    # === 2. 對 port 8555 列常見路徑 ===
    media_base = f"https://{nvr['host']}:8555"
    print("[INFO] 探勘 port 8555 ...")

    # 注入真實 session token
    PROBE_QUERY_TEMPLATE["session"] = session_token

    results = []
    for path in CANDIDATE_PATHS:
        r = probe_path(scanner.session, media_base, path, PROBE_TIMEOUT)
        results.append(r)

    # === 3. 報表 ===
    print_table(nvr, results)

    # === 4. 簡要判讀 ===
    print("[判讀]")
    interesting = [
        r for r in results if isinstance(r["status"], int) and 200 <= r["status"] < 400
    ]
    auth_401 = [r for r in results if r["status"] == 401]
    not_found = [r for r in results if r["status"] == 404]
    binary_stream = [
        r
        for r in results
        if isinstance(r["status"], int)
        and r["status"] == 200
        and any(
            s in r["content_type"].lower()
            for s in ("mp4", "mpeg", "video", "octet-stream", "mjpeg", "h264", "hls")
        )
    ]
    if interesting:
        print(f"  ✓ {len(interesting)} 條路徑回 2xx/3xx：")
        for r in interesting:
            print(f"    {r['path']:<40s} → {r['status']} {r['content_type']}")
    if binary_stream:
        print(f"  ⭐ {len(binary_stream)} 條看起來是 binary stream（影片/M-JPEG）：")
        for r in binary_stream:
            print(f"    {r['path']:<40s} → {r['content_type']}")
    if auth_401:
        print(f"  ⚠ {len(auth_401)} 條回 401（session 沒被接受；可能 8555 需另外認證）")
    if not_found == results:
        print("  ✗ 全 404 — port 8555 可能不是 Avigilon，或根本沒開 Media 服務")

    # 存 Markdown
    report_path = base_dir / "docs" / "media-api-probe.md"
    report_path.parent.mkdir(exist_ok=True)
    save_markdown(nvr, results, report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
