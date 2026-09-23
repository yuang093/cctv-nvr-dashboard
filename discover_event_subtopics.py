"""
discover_event_subtopics.py
================================
步驟 0：事件字典探勘（v2 — 使用 Avigilon Web Endpoint 認證）

用途：呼叫 Avigilon NVR 的 /mt/api/rest/v1/event-subtopics 端點，
取得 NVR 實際支援的所有事件主題字串，作為後續 AvigilonScanner
過濾邏輯（VIDEO_LOSS / TAMPER / BLIND / SCENE_CHANGE）的真實依據。

認證：
    與 nvr_scanner.py 共用 — 從環境變數或 .env 讀 userNonce / userKey /
    integrationId（規格見 Avigilon_API_Reference.md §1）。

執行方式：
    python discover_event_subtopics.py
    或在 .env 設好金鑰後直接跑

行為：
    自動讀取同目錄下的 nvr_config.json，使用「第一台 NVR」
    （index 0）進行連線測試。完成後於終端機印出分類報表。

錯誤處理：
    所有例外統一由 DiscoveryError 包裝，並印至 stderr。
    涵蓋：ScannerError（認證 / 連線 / API 錯誤）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from nvr_scanner import (
    AvigilonScanner,
    ScannerError,
    load_env_file,
    unwrap_response,
    _extract_list,
    ABNORMAL_KEYWORDS,
)


CONFIG_FILENAME = "nvr_config.json"
EVENT_SUBTOPICS_PATH = "/mt/api/rest/v1/event-subtopics"


# === 自訂例外 ===
class DiscoveryError(Exception):
    """事件字典探勘過程中的所有錯誤都包裝為此例外。"""


# === 工具函式 ===
def load_first_nvr(config_path: Path) -> dict:
    """
    載入 nvr_config.json 並回傳第一台 NVR 設定。

    Raises:
        DiscoveryError: 設定檔不存在、JSON 格式錯、或無 nvr_servers。
    """
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as exc:
        raise DiscoveryError(f"找不到設定檔：{config_path}") from exc
    except json.JSONDecodeError as exc:
        raise DiscoveryError(f"設定檔 JSON 格式錯誤：{exc}") from exc

    servers = config.get("nvr_servers", [])
    if not servers:
        raise DiscoveryError("設定檔中沒有任何 nvr_servers 項目")
    return cast(dict[str, Any], servers[0])


def normalize_topics(data: Any) -> list[str]:
    """
    將 API 回傳的各種可能結構攤平為 list[str]。

    支援（依序）：
        - 直接 list[str]
        - list[dict{"subtopic"|"name"|"topic": "..."}]
        - dict 包裝於 results / data / topics / eventSubtopics / subtopics
        - dict singleton（自動解開）
        - dict 中所有字串值（fallback）

    Args:
        data: 任意型別的 API 回應。

    Returns:
        攤平後的字串清單。
    """
    # 先解開 {status: success, result: ...} 外層包裝
    data = unwrap_response(data)
    # 再用 nvr_scanner._extract_list 解開常見包裝
    candidates = _extract_list(data, "eventSubtopics", "subtopics", "topics", "items")
    topics: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            topics.append(item)
        elif isinstance(item, dict):
            t = item.get("subtopic") or item.get("name") or item.get("topic")
            if t:
                topics.append(str(t))
    if topics:
        return topics
    # fallback：dict 中所有字串值
    if isinstance(data, dict):
        return [v for v in data.values() if isinstance(v, str)]
    return []


def classify(topics: list[str]) -> tuple[list[str], list[str]]:
    """
    將主題分類為「異常候選」與「其他」。

    Args:
        topics: 完整主題清單。

    Returns:
        (異常候選清單, 其他清單)。
    """
    abnormal: list[str] = []
    others: list[str] = []
    abnormal_set = set(ABNORMAL_KEYWORDS)
    for t in topics:
        if t in abnormal_set:
            abnormal.append(t)
        else:
            others.append(t)
    return abnormal, others


def print_report(nvr: dict, topics: list[str], abnormal: list[str]) -> None:
    """終端機報表輸出。"""
    title = f"NVR 事件字典探勘報告：{nvr.get('name', '?')} ({nvr.get('host', '?')})"
    print("=" * 60)
    print(title)
    print("=" * 60)
    print(f"總事件主題數：{len(topics)}")
    print()
    print("[異常候選]（已預期為異常事件）")
    if abnormal:
        for t in abnormal:
            print(f"  - {t}")
    else:
        print(f"  （NVR 回傳中找不到 {ABNORMAL_KEYWORDS} 任何一個）")
    print()
    print("[其他事件主題]（非預期異常，僅列出供您判斷）")
    others = [t for t in topics if t not in abnormal]
    if others:
        for t in others:
            print(f"    {t}")
    else:
        print("  （無）")
    print()
    print("-" * 60)
    print("下一步：")
    print("  1. ABNORMAL_KEYWORDS 已預設 ACC 8.7 常用 DEVICE_* 主題")
    print("     （DEVICE_VIDEO_SIGNAL_LOST / TAMPERING / COMMUNICATION_LOST")
    print("      / LONG_FAILED / DISCONNECTED / CONNECTION_ERROR /")
    print("      ANOMALY_START / UNUSUAL_STARTED）")
    print("  2. 若 [異常候選] 為空，代表 ACC 用不同主題，")
    print("     從 [其他事件主題] 找出 DEVICE_* 開頭的補上")
    print("  3. 拔網路線 / 斷電測試：")
    print("     scanner 會用 connectionStatus.state 雙重檢查，")
    print("     LONG_FAILED / DISCONNECTED / ERROR 都會被標異常")


def main() -> int:
    """主流程：讀設定 → 讀金鑰 → 用 AvigilonScanner 登入 → 探勘 → 報表。"""
    base_dir = Path(__file__).parent

    # --- 載入 .env（沿用 nvr_scanner 的 .env 讀取邏輯） ---
    loaded = load_env_file(base_dir / ".env")
    if loaded:
        print(f"[INFO] 已從 .env 載入 {loaded} 個環境變數")

    # --- 載入 NVR 設定 ---
    try:
        nvr = load_first_nvr(base_dir / CONFIG_FILENAME)
    except DiscoveryError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    # --- 讀取認證金鑰（環境變數優先） ---
    import os

    user_nonce = os.environ.get("AVIGILON_USER_NONCE", "")
    user_key = os.environ.get("AVIGILON_USER_KEY", "")
    integration_id = os.environ.get("AVIGILON_INTEGRATION_ID", "")

    if not user_nonce or not user_key:
        print(
            "[FAIL] 需要 AVIGILON_USER_NONCE / AVIGILON_USER_KEY "
            "（從 .env 設或 export）",
            file=sys.stderr,
        )
        return 1

    # --- 帳密：環境變數可覆寫 nvr_config.json ---
    nvr = dict(nvr)
    nvr["username"] = os.environ.get("AVIGILON_USERNAME") or nvr["username"]
    nvr["password"] = os.environ.get("AVIGILON_PASSWORD") or nvr["password"]

    print(
        f"[INFO] 目標 NVR：{nvr.get('name')} @ "
        f"https://{nvr['host']}:{nvr.get('port', 8443)}"
    )

    # --- 用 AvigilonScanner 登入並探勘 ---
    try:
        scanner = AvigilonScanner(
            nvr,
            user_nonce=user_nonce,
            user_key=user_key,
            integration_id=integration_id,
        )
        scanner.login()  # 失敗會拋 ScannerError
        server_id = scanner.get_server_ids()

        # 從 AvigilonScanner 內部直接呼叫 event-subtopics（無公開 method）
        data = scanner.session.get(
            f"{scanner.base_url}{EVENT_SUBTOPICS_PATH}",
            params={"session": scanner._session_token, "serverId": server_id},
            timeout=scanner.timeout,
            verify=scanner.verify_ssl,
        )
        if not data.ok:
            raise DiscoveryError(
                f"event-subtopics 回傳 HTTP {data.status_code}：{data.text[:200]}"
            )
        topics = normalize_topics(unwrap_response(data.json()))
    except ScannerError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except DiscoveryError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    if not topics:
        print("[WARN] event-subtopics 回傳空清單，請手動確認 NVR 權限。")

    abnormal, _ = classify(topics)
    print()
    print_report(nvr, topics, abnormal)
    return 0


if __name__ == "__main__":
    sys.exit(main())
