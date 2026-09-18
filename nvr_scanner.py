"""
nvr_scanner.py
================
Avigilon NVR 攝影機狀態掃描器（Worker, v1）。

依據：
    Avigilon_API_Reference.md（本專案 API 規格文件）
    class_interface.md（介面契約）

核心流程（per-NVR instance）：
    1. login()        — POST /mt/api/rest/v1/login，帶 SHA-256 authorizationToken
    2. get_server_ids()  — GET /mt/api/rest/v1/server/ids
    3. get_cameras()     — GET /mt/api/rest/v1/cameras
    4. get_active_events() — GET /mt/api/rest/v1/events/search?queryType=ACTIVE
    5. scan()         — 完整流程 + 過濾 VIDEO_LOSS / TAMPER / BLIND / SCENE_CHANGE
    6. print_report() — 終端機印出設備狀態清單

認證機密輸入（讀取優先順序由高到低）：
    1. Shell 環境變數（`export AVIGILON_USER_KEY=...`）
    2. 同目錄 `.env` 檔（不覆蓋 shell 環境變數；格式見 `load_env_file()`）
    3. 互動輸入（密碼用 getpass 隱藏）
    4. `nvr_config.json` 內 NVR 設定（僅 username / password）

    環境變數：
        AVIGILON_USER_NONCE     — 開發者金鑰（向 Avigilon Developer Program 申請）
        AVIGILON_USER_KEY       — 開發者金鑰（getpass 隱藏輸入）
        AVIGILON_INTEGRATION_ID — 選填，無則留空字串
        AVIGILON_USERNAME       — 選填覆蓋項，未設時讀 nvr_config.json
        AVIGILON_PASSWORD       — 選填覆蓋項，未設時讀 nvr_config.json

    .env 範本：複製 `.env.example` 為 `.env` 後填入實際金鑰。
    注意：`.env` 已在 `.gitignore` 內，**絕對不要 commit**。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib3
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any

import requests

from db.sqlite_writer import SqliteWriter
from webhook import substitute_env_vars

# === 常數 ===
CONFIG_FILENAME = "nvr_config.json"

# Avigilon ACC 8.7+ 端點全部位於 /mt/api/rest/v1/ 命名空間（實測確認）
LOGIN_PATH = "/mt/api/rest/v1/login"
SERVER_IDS_PATH = "/mt/api/rest/v1/server/ids"
CAMERAS_PATH = "/mt/api/rest/v1/cameras"
EVENTS_SEARCH_PATH = "/mt/api/rest/v1/events/search"
TIMELINE_PATH = "/mt/api/rest/v1/timeline"

# 連線與讀取逾時秒數
DEFAULT_TIMEOUT = 10

# 視為「異常」的事件主題關鍵字（與 Avigilon_API_Reference.md §4 對齊）
# 依 ACC 8.7 event-subtopics 實測結果，使用 DEVICE_* 開頭的實際主題。
# 連線/斷線類另由 cameras.connectionStatus.state 雙重檢查（見 scan()）。
ABNORMAL_KEYWORDS = (
    # 影像訊號 / 視訊問題
    "DEVICE_VIDEO_SIGNAL_LOST",
    # 破壞 / 遮蔽
    "DEVICE_TAMPERING",
    # 連線 / 通訊（雖 connectionStatus 已涵蓋，events 加上更保險）
    "DEVICE_COMMUNICATION_LOST",
    "DEVICE_CONNECTION_ERROR",
    "DEVICE_LONG_FAILED",
    "DEVICE_DISCONNECTED",
    # 影像分析異常（場景變更、未預期活動）
    "DEVICE_ANOMALY_START",
    "DEVICE_UNUSUAL_STARTED",
)

# 客戶端識別（伺服器可能會校驗）
DEFAULT_CLIENT_NAME = "PythonScannerApp"
# ACC 版本字串：實測對齊 NVR 實際版本（從 /mt/api/rest/v1/health?v 取）
# 預設 8.7.3.4 對應 ACC 8.7 系列；之後可用 nvr_config.json 覆寫
DEFAULT_CLIENT_VERSION = "8.7.3.4"


# === 自訂例外（依 class_interface.md 契約） ===
class ScannerError(Exception):
    """AvigilonScanner 所有錯誤的根例外。"""


class AuthError(ScannerError):
    """認證 / 授權失敗（HTTP 401/403、token 缺值、密碼錯等）。"""


class ConnectionError_(ScannerError):
    """網路層錯誤（timeout / SSL / 連線失敗）。命名避開內建 ConnectionError。"""


class ApiResponseError(ScannerError):
    """API 回應格式錯誤或非預期狀態碼。"""


# === 認證：動態 Token 生成 ===
def compute_authorization_token(
    user_nonce: str,
    user_key: str,
    timestamp: int | None = None,
    integration_id: str = "",
) -> str:
    """
    依 Avigilon 規格計算 authorizationToken。

    規格（見 Avigilon_API_Reference.md §1）：
        hexEncoded = SHA-256( str(timestamp) + userKey ).hexdigest()
        authorizationToken = f"{user_nonce}:{timestamp}:{hexEncoded}:{integration_id}"

    Args:
        user_nonce: 由 Avigilon 核發給開發者的金鑰之一。
        user_key: 由 Avigilon 核發給開發者的金鑰之一。
        timestamp: Unix Time（秒）。None 時自動取當下時間。
        integration_id: 整合識別碼；無則留空字串。

    Returns:
        組合完成的 authorizationToken 字串。

    Raises:
        AuthError: 必要輸入為空。
    """
    if not user_nonce or not user_key:
        raise AuthError("userNonce 與 userKey 為必填")
    if timestamp is None:
        timestamp = int(time.time())
    # 注意：timestamp 必須是純數字字串拼接，不加任何分隔符或時區
    hex_encoded = hashlib.sha256(f"{timestamp}{user_key}".encode("utf-8")).hexdigest()
    return f"{user_nonce}:{timestamp}:{hex_encoded}:{integration_id}"


# === 工具函式 ===
def load_env_file(path: Path) -> int:
    """
    從 .env 風格檔載入環境變數（不覆蓋已存在的環境變數）。

    格式（與 python-dotenv / docker-compose 慣例相容）：
        # 註解行
        KEY=value
        KEY="quoted value with spaces"
        KEY='single quoted'

    Args:
        path: .env 檔路徑。

    Returns:
        成功載入的變數數量（檔案不存在回 0）。
    """
    if not path.exists():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 去掉包圍的單/雙引號
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # 不覆蓋既有環境變數（保留 shell export 的優先權）
        if key and os.environ.get(key) is None:
            os.environ[key] = value
            count += 1
    return count


def get_credential(env_var: str, prompt: str, *, hide: bool = False) -> str:
    """
    從環境變數讀取認證資料；若未設置則改為互動輸入。

    Args:
        env_var: 環境變數名稱。
        prompt: 互動輸入時的提示文字。
        hide: True 時使用 getpass（密碼欄位）。

    Returns:
        讀取到的字串值。
    """
    val = os.environ.get(env_var)
    if val:
        return val
    if hide:
        return getpass(prompt)
    return input(prompt)


def make_session(verify_ssl: bool) -> requests.Session:
    """
    建立 requests.Session，設定 SSL 與警告抑制。

    Args:
        verify_ssl: True=驗證 SSL；False=跳過（NVR 自簽證書用）。

    Returns:
        配置好的 Session 物件。
    """
    session = requests.Session()
    session.verify = verify_ssl
    if not verify_ssl:
        # 自簽證書時關閉 InsecureRequestWarning，避免洗版
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def call_api(
    session: requests.Session,
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    json_body: dict | None = None,
    params: dict | None = None,
) -> Any:
    """
    通用 HTTP API 呼叫器，回傳解析後的 JSON。

    Args:
        session: 已配置的 requests.Session。
        base_url: NVR 的 base URL（不含 path）。
        path: API 路徑（須以 / 開頭）。
        method: HTTP method（GET / POST）。
        json_body: POST body（dict，自動序列為 JSON）。
        params: query string 參數。

    Returns:
        解析後的 JSON（型別依 API 而定，list 或 dict）。

    Raises:
        ConnectionError_: 連線逾時 / SSL 錯 / 連線失敗 / 其他 requests 例外。
        AuthError: HTTP 401/403。
        ApiResponseError: 非預期狀態碼或回應非 JSON。
    """
    url = f"{base_url}{path}"
    try:
        resp = session.request(
            method,
            url,
            json=json_body,
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.Timeout as exc:
        raise ConnectionError_(f"連線逾時（{DEFAULT_TIMEOUT}s）：{url}") from exc
    except requests.exceptions.SSLError as exc:
        raise ConnectionError_(f"SSL 錯誤：{exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError_(f"無法連線到 {url}：{exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise ConnectionError_(f"HTTP 請求失敗：{exc}") from exc

    if resp.status_code in (401, 403):
        # 401/403 一律視為授權問題，由 AuthError 統一拋出
        raise AuthError(f"授權失敗（HTTP {resp.status_code}）：{resp.text[:200]}")
    if not resp.ok:
        raise ApiResponseError(
            f"API 回傳非預期狀態（HTTP {resp.status_code}）：{resp.text[:200]}"
        )

    try:
        return resp.json()
    except ValueError as exc:
        raise ApiResponseError(f"回應不是合法 JSON：{resp.text[:200]}") from exc


def unwrap_response(data: Any) -> Any:
    """
    解開 Avigilon 常見的回應包裝：`{"status": "success", "result": ...}`。

    ACC Web Endpoint 把實際資料包在 `result`（有時用 `data`）底下，並以
    `status: success` 表示成功。失敗回應則不會符合此格式（會被 call_api 擋下）。
    若資料不符合包裝格式則原樣回傳。

    Args:
        data: 解析後的 JSON（call_api 回傳值）。

    Returns:
        解開後的內容（可能是 dict / list）。
    """
    if not isinstance(data, dict):
        return data
    if data.get("status") == "success":
        for key in ("result", "data", "payload"):
            if key in data and data[key] is not None:
                return data[key]
    return data


def _extract_list(data: Any, *wrapper_keys: str) -> list:
    """
    從可能的包裝結構中提取 list 內容（用於 cameras / events 等清單型端點）。

    支援的形狀：
        - 直接 list: [...]
        - dict 包裝：`{"cameras": [...]}`, `{"items": [...]}`, `{"events": [...]}` 等
        - dict singleton：`{<key>: [...]}`（自動解開唯一的 list 值）
        - dict-of-objects：`{"id1": {...}, "id2": {...}}` → 取所有 value 組成 list

    Args:
        data: 解析後的 JSON。
        wrapper_keys: 常見的包裝 key 名稱（會依序嘗試）。

    Returns:
        提取出的 list（若完全無法提取則回空 list）。
    """
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    # 先試常見包裝 key
    for key in wrapper_keys:
        if key in data and isinstance(data[key], list):
            return data[key]
    # dict singleton 且唯一 value 是 list → 解開
    if len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, list):
            return inner
    # dict-of-objects 形式 → 取所有 dict value
    return [v for v in data.values() if isinstance(v, dict)]


# === AvigilonScanner Class（per-NVR instance） ===
class AvigilonScanner:
    """
    對應單一 NVR 的掃描器。

    設計原則（見 class_interface.md）：
        - 每個 instance 只負責一台 NVR，掃完銷毀，不跨 NVR 共用 HTTP session。
        - 不直接寫 DB；呼叫 save_to_db()（v2 範疇，本版本未實作）。
        - DB 寫入層採依賴注入（IDatabaseWriter Protocol，v2 範疇）。
    """

    def __init__(
        self,
        nvr_config: dict,
        *,
        user_nonce: str,
        user_key: str,
        integration_id: str = "",
        client_name: str = DEFAULT_CLIENT_NAME,
        client_version: str = DEFAULT_CLIENT_VERSION,
        timeout: int = DEFAULT_TIMEOUT,
        verify_ssl: bool = False,
        page_size: int = 100,  # 一次最多取幾台相機 / 幾筆事件
        session: requests.Session | None = None,
    ):
        """
        Args:
            nvr_config: 來自 nvr_config.json 的單台 NVR 設定。
            user_nonce: Avigilon 開發者金鑰之一。
            user_key: Avigilon 開發者金鑰之一。
            integration_id: 選填整合識別碼。
            client_name: 用於 Authorization 階段的 clientName 欄位。
            client_version: 用於對齊 ACC 版本的 clientVersion 字串。
            timeout: HTTP 逾時秒數。
            verify_ssl: 是否驗證 SSL（NVR 多為自簽，預設 False）。
            page_size: cameras / events 的單頁大小（避免預設太小漏資料）。
            session: 可注入的 requests.Session（測試用）。
        """
        self.nvr = nvr_config
        self.user_nonce = user_nonce
        self.user_key = user_key
        self.integration_id = integration_id
        self.client_name = client_name
        self.client_version = client_version
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._page_size = page_size
        self.base_url = f"https://{nvr_config['host']}:{nvr_config.get('port', 8443)}"
        self.session = session or make_session(verify_ssl)

        # 內部狀態（登入後填入）
        self._session_token: str | None = None
        self._server_id: str | None = None

    # --- 認證 ---
    def login(self) -> str:
        """
        POST /mt/api/rest/v1/login，回傳 session token。

        內部自動產生 SHA-256 authorizationToken（規格見 Avigilon_API_Reference.md §1）。

        Returns:
            session token 字串。

        Raises:
            AuthError: 登入失敗或回應中無 session 欄位。
        """
        auth_token = compute_authorization_token(
            self.user_nonce,
            self.user_key,
            integration_id=self.integration_id,
        )
        body = {
            "username": self.nvr.get("username", ""),
            "password": self.nvr.get("password", ""),
            "clientName": self.client_name,
            "clientVersion": self.client_version,
            "authorizationToken": auth_token,
        }
        data = call_api(
            self.session,
            self.base_url,
            LOGIN_PATH,
            method="POST",
            json_body=body,
        )
        # 解開 {status: success, result: ...} 包裝
        payload = unwrap_response(data)
        # 相容不同版本的欄位命名（result.session / session / Session / token）
        token = payload.get("session") or payload.get("Session") or payload.get("token")
        if not token:
            raise AuthError(f"登入回應中找不到 session token：{data}")
        self._session_token = token
        self._server_id = None  # 重新登入時清掉舊的 serverId
        return token

    # --- 伺服器 ID ---
    def get_server_ids(self) -> str:
        """
        GET /mt/api/rest/v1/server/ids，回傳 serverId。

        回傳值會被快取於 self._server_id，後續 cameras / events 會複用。

        Returns:
            serverId 字串。

        Raises:
            ScannerError: 尚未登入。
            ApiResponseError: 回應無法解析。
        """
        if not self._session_token:
            raise ScannerError("請先呼叫 login() 取得 session token")
        if self._server_id is None:
            data = call_api(
                self.session,
                self.base_url,
                SERVER_IDS_PATH,
                params={"session": self._session_token},
            )
            self._server_id = self._parse_server_id(unwrap_response(data))
        return self._server_id

    @staticmethod
    def _parse_server_id(data: Any) -> str:
        """相容處理 server/ids 的各種回傳結構。

        支援的結構（經 unwrap_response 解開後）：
        - `{"servers": [{"id": "..."}]}` — ACC 7+ 多伺服器站點
        - `{"servers": [{"serverId": "..."}]}` — ACC 6.x 多伺服器站點
        - `{"serverId": "..."}` — 單機 NVR (serverId 欄位)
        - `{"id": "..."}` — 單機 NVR (id 欄位)
        - `[{...}]` — 直接 list（舊版）
        - `"<id>"` — 裸字串

        ACC 7+ 之前版本只處理最後 3 種，遇到 `{"servers": [...]}` 會 fallback
        到 `str(None)` 而回傳字串 "None"，造成下游 API 帶錯 serverId。
        """
        if isinstance(data, list):
            if not data:
                raise ApiResponseError("server/ids 回傳空陣列")
            return AvigilonScanner._parse_server_id(data[0])
        if isinstance(data, dict):
            # ACC 7+ / 6.x 多伺服器：解開 servers 陣列取第一個
            if "servers" in data:
                servers = data["servers"]
                if not isinstance(servers, list) or not servers:
                    raise ApiResponseError(
                        f"server/ids 的 servers 欄位為空或型別錯：{servers!r}"
                    )
                return AvigilonScanner._parse_server_id(servers[0])
            # 單機 NVR：直接從 root 拿 id
            for key in ("serverId", "id", "Id"):
                if data.get(key):
                    return str(data[key])
            raise ApiResponseError(f"無法解析 server/ids 回應：{data}")
        if isinstance(data, str):
            return data
        raise ApiResponseError(f"無法解析 server/ids 回應：{data!r}")

    # --- 攝影機 ---
    def get_cameras(self) -> dict[str, str]:
        """
        GET /mt/api/rest/v1/cameras，回傳 {deviceId: cameraName}。

        Returns:
            攝影機 ID 對應名稱的字典。

        Raises:
            ScannerError: 尚未登入。
            ApiResponseError: 回應無法解析。
        """
        if not self._session_token:
            raise ScannerError("請先呼叫 login() 取得 session token")
        server_id = self.get_server_ids()  # lazy 取得
        data = call_api(
            self.session,
            self.base_url,
            CAMERAS_PATH,
            params={
                "session": self._session_token,
                "serverId": server_id,
                "pageSize": self._page_size,  # 避免預設值太小漏資料
            },
        )
        return self._parse_cameras(unwrap_response(data))

    @staticmethod
    def _parse_cameras(data: Any) -> dict[str, dict]:
        """
        相容處理 cameras 的各種回傳結構，回傳豐富的相機資訊 dict。

        Returns:
            {deviceId: {
                "name": str,
                "connection_state": str,  # CONNECTED / DISCONNECTED / UNKNOWN ...
                "available": bool,
            }}
        """
        cameras: dict[str, dict] = {}
        for cam in _extract_list(data, "cameras", "items"):
            if not isinstance(cam, dict):
                continue
            dev_id = cam.get("deviceId") or cam.get("id")
            if dev_id is None:
                continue
            name = (
                cam.get("name")
                or cam.get("cameraName")
                or cam.get("logicalId")
                or dev_id
            )
            cs = cam.get("connectionStatus") or {}
            cameras[str(dev_id)] = {
                "name": str(name),
                "connection_state": str(cs.get("state", "UNKNOWN")),
                "available": bool(cam.get("available", False)),
                "ip_address": cam.get("ipAddress"),
                "mac_address": cam.get("physicalAddress"),
            }
        return cameras

    # --- 事件 ---
    def get_active_events(self) -> list[dict]:
        """
        GET /mt/api/rest/v1/events/search?queryType=ACTIVE，回傳 ACTIVE 事件清單。

        Returns:
            事件字典清單（原始格式，不過濾）。

        Raises:
            ScannerError: 尚未登入。
        """
        if not self._session_token:
            raise ScannerError("請先呼叫 login() 取得 session token")
        server_id = self.get_server_ids()
        data = call_api(
            self.session,
            self.base_url,
            EVENTS_SEARCH_PATH,
            params={
                "session": self._session_token,
                "serverId": server_id,
                "queryType": "ACTIVE",
                "pageSize": self._page_size,  # 防禦性：避免 ACTIVE 事件太多被截斷
            },
        )
        return self._parse_events(unwrap_response(data))

    # --- 錄影時間軸 ---
    def get_timeline(
        self,
        camera_id: str,
        *,
        from_iso: str,
        to_iso: str,
        scope: str = "100_SECONDS",
    ) -> dict:
        """
        GET /mt/api/rest/v1/timeline，回傳單台 cam 的錄影時間軸。

        Args:
            camera_id: NVR 原始 deviceId。
            from_iso: 視窗開始（UTC ISO 8601 字串）。
            to_iso: 視窗結束（UTC ISO 8601 字串）。
            scope: NVR 提供的 timeline 粒度（預設 `100_SECONDS`，視窗 ≤ 24h 時適用）。

        Returns:
            unwrap 過的 dict：`{"timelines": [{"cameraId": "...", "record": [{"start", "end"}, ...]}]}`
            結構容錯由 `web.timeline.parse_timeline_response` 處理。

        Raises:
            ScannerError: 尚未登入。
            ApiResponseError: 回應無法解析。
        """
        if not self._session_token:
            raise ScannerError("請先呼叫 login() 取得 session token")
        server_id = self.get_server_ids()  # _parse_server_id 已支援 ACC 7+
        raw = call_api(
            self.session,
            self.base_url,
            TIMELINE_PATH,
            params={
                "session": self._session_token,
                "serverId": server_id,
                "cameraIds": camera_id,
                "from": from_iso,
                "to": to_iso,
                "scope": scope,
            },
        )
        return unwrap_response(raw)

    @staticmethod
    def _parse_events(data: Any) -> list[dict]:
        """相容處理 events/search 的各種回傳結構。"""
        events: list[dict] = []
        for ev in _extract_list(data, "events", "results", "items"):
            if isinstance(ev, dict):
                events.append(ev)
        return events

    # --- 過濾與掃描 ---
    @staticmethod
    def _is_abnormal(event: dict) -> bool:
        """
        判斷單筆事件是否符合異常關鍵字（規格 Avigilon_API_Reference.md §4）。

        檢查兩個欄位：
            - eventTopics（list[str]）
            - eventTopic（str，舊版或單值）
        """
        topics = event.get("eventTopics") or []
        if isinstance(topics, str):
            topics = [topics]
        for t in topics:
            if any(kw in str(t) for kw in ABNORMAL_KEYWORDS):
                return True
        single = event.get("eventTopic")
        if single and any(kw in str(single) for kw in ABNORMAL_KEYWORDS):
            return True
        return False

    # --- Media API：抓 jpeg 縮圖（Phase 2.8 影像健康巡檢用） ---
    def fetch_thumbnail(
        self,
        camera_id: str,
        at_time: datetime | None = None,
    ) -> bytes | None:
        """
        GET /mt/api/rest/v1/media?format=jpeg 回傳 JPEG bytes。

        Phase 2.8（Arisan 影像健康巡檢）用：worker 抓 2 張（間隔 5s）→ analyze_image + is_frozen。

        Args:
            camera_id: Avigilon deviceId。
            at_time: UTC 時間；None = 當下（live）。

        Returns:
            JPEG bytes，或 None（任何錯誤，避免 batch 中斷）。

        Note:
            不沿用 call_api（call_api 走 .json()，jpeg 是 binary）。
            與 web/clip_retrieval.py 的 MpdMediaClient.get_snapshot 同目的，
            故意不 import 該模組以避免 worker ↔ web 跨 app 依賴（30 行重複代價換架構乾淨）。
            規格：docs/media-api-research.md §2。
        """
        if self._session_token is None:
            return None
        params: dict = {
            "session": self._session_token,
            "cameraId": camera_id,
            "format": "jpeg",
        }
        if at_time is None:
            params["t"] = "live"
        else:
            params["t"] = at_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            resp = self.session.request(
                "GET",
                f"{self.base_url}/mt/api/rest/v1/media",
                params=params,
                timeout=self.timeout,
            )
            if not resp.ok or len(resp.content) == 0:
                return None
            # JPEG magic bytes 快速驗證（避免拿到 HTML error page 誤判）
            if not resp.content.startswith(b"\xff\xd8\xff"):
                return None
            return resp.content
        except Exception:
            return None

    def fetch_thumbnail_with_status(
        self,
        camera_id: str,
        at_time: datetime | None = None,
    ) -> tuple[bytes | None, str | None]:
        """2026-07-30：與 fetch_thumbnail 同邏輯，但回傳 (bytes, error_msg)。

        給 batch_scan verbose 用，方便未來診斷「為何某 cam 抓不到 jpeg」。
        例如：
          - ("", None) → 未登入
          - (None, "HTTP 403") → NVR 拒絕（cam 沒被 manage）
          - (None, "HTTP 404") → NVR 找不到 endpoint
          - (None, "non-JPEG response (123 bytes)") → NVR 回 HTML 錯誤頁
          - (None, "ConnectionError: ...") → 網路問題
          - (jpeg_bytes, None) → 成功
        """
        if self._session_token is None:
            return None, "尚未登入（_session_token is None）"
        params: dict = {
            "session": self._session_token,
            "cameraId": camera_id,
            "format": "jpeg",
        }
        if at_time is None:
            params["t"] = "live"
        else:
            params["t"] = at_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            resp = self.session.request(
                "GET",
                f"{self.base_url}/mt/api/rest/v1/media",
                params=params,
                timeout=self.timeout,
            )
            if not resp.ok:
                return None, f"HTTP {resp.status_code} {resp.reason}"
            if len(resp.content) == 0:
                return None, "HTTP 200 但 0 bytes（cam 沒串流？）"
            if not resp.content.startswith(b"\xff\xd8\xff"):
                return None, (
                    f"非 JPEG 回應（{len(resp.content)} bytes，magic={resp.content[:4]!r}）"
                )
            return resp.content, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def scan(self) -> dict:
        """
        完整掃描一台 NVR，回傳結構化結果。

        流程：login → get_server_ids → get_cameras → get_active_events → 過濾。

        異常判定採**雙重檢查**（任一符合即標異常）：
            1. ACTIVE 事件匹配 `ABNORMAL_KEYWORDS`（VIDEO_LOSS / TAMPER / BLIND / SCENE_CHANGE）
            2. 相機 `connectionStatus.state` 不是 CONNECTED（防 ACC events 未生成）

        Returns:
            {
                "nvr_id": str,
                "nvr_name": str,
                "cameras": {deviceId: {name, connection_state, available}},
                "events": [abnormal events（含來自 connection_status 合成的事件）],
                "stats": {
                    "total_cameras": int,
                    "abnormal_cameras": int,
                },
            }
        """
        self.login()  # 若失敗直接拋出
        cameras = self.get_cameras()
        all_events = self.get_active_events()

        # --- 異常來源 1: ACTIVE 事件匹配關鍵字 ---
        abnormal_events = [e for e in all_events if self._is_abnormal(e)]
        event_abnormal_ids = {
            str(e.get("deviceId"))
            for e in abnormal_events
            if e.get("deviceId") is not None
        }

        # --- 異常來源 2: connectionStatus.state 非 CONNECTED ---
        # 即使 ACC 沒生對應事件，只要相機狀態非 CONNECTED 就標異常
        # 去重：若該相機已有 event 異常就不重複加（避免報表同一台相機出現兩次）
        state_abnormal_ids: set[str] = set()
        state_abnormal_events: list[dict] = []
        for dev_id, info in cameras.items():
            if (
                info["connection_state"] != "CONNECTED"
                and dev_id not in event_abnormal_ids
            ):
                state_abnormal_ids.add(dev_id)
                state_abnormal_events.append(
                    {
                        "deviceId": dev_id,
                        "eventTopics": [f"STATE_{info['connection_state']}"],
                        "eventTopic": f"STATE_{info['connection_state']}",
                        "source": "camera_state",
                        "connection_state": info["connection_state"],
                    }
                )

        all_abnormal_ids = event_abnormal_ids | state_abnormal_ids
        all_abnormal_events = abnormal_events + state_abnormal_events

        return {
            "nvr_id": self.nvr.get("id", "?"),
            "nvr_name": self.nvr.get("name", "?"),
            "cameras": cameras,
            "events": all_abnormal_events,
            "stats": {
                "total_cameras": len(cameras),
                "abnormal_cameras": len(all_abnormal_ids),
            },
        }


# === 終端機報表 ===
def _camera_name(cam_info: Any) -> str:
    """相容舊版（純字串）與新版（dict 含 name）兩種 cameras 值。"""
    if isinstance(cam_info, dict):
        return cam_info.get("name", "?")
    return str(cam_info)


def print_report(result: dict) -> None:
    """
    印出單台 NVR 的掃描結果報表。

    Args:
        result: AvigilonScanner.scan() 的回傳結構。
    """
    nvr_id = result["nvr_id"]
    nvr_name = result["nvr_name"]
    cameras = result["cameras"]
    events = result["events"]
    stats = result["stats"]

    print("=" * 60)
    print(f"NVR 掃描報表：{nvr_name} ({nvr_id})")
    print("=" * 60)
    print(f"總攝影機數：{stats['total_cameras']}")
    print(f"異常攝影機數：{stats['abnormal_cameras']}")
    print()

    if not events:
        print("[OK] 目前無 ACTIVE 異常事件")
    else:
        print("[異常事件]")
        for ev in events:
            dev_id = str(ev.get("deviceId", "?"))
            cam_name = _camera_name(cameras.get(dev_id, "(未知)"))
            topics = ev.get("eventTopics") or [ev.get("eventTopic", "?")]
            topics_str = ", ".join(str(t) for t in topics)
            src = ev.get("source")
            extra = f" [來源: {src}]" if src and src != "events" else ""
            print(f"  - {cam_name} ({dev_id}): {topics_str}{extra}")
    print()

    print("-" * 60)
    print("設備狀態清單：")
    if not cameras:
        print("  （無）")
    else:
        abnormal_ids = {
            str(e.get("deviceId")) for e in events if e.get("deviceId") is not None
        }
        for dev_id, cam_info in cameras.items():
            cam_name = _camera_name(cam_info)
            state = (
                cam_info.get("connection_state", "?")
                if isinstance(cam_info, dict)
                else "?"
            )
            marker = "[異常]" if dev_id in abnormal_ids else "[正常]"
            print(f"  {marker} {cam_name} ({dev_id})  state={state}")


# === 主流程 ===
def _load_config(config_path: Path) -> dict:
    """
    載入 nvr_config.json，過濾 enabled=True 的 NVR。

    Args:
        config_path: 設定檔路徑。

    Returns:
        過濾後的 NVR 清單（已驗證必填欄位）。

    Raises:
        ScannerError: 設定檔缺漏、JSON 格式錯、或無 enabled NVR。
    """
    try:
        raw = config_path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
    except FileNotFoundError as exc:
        raise ScannerError(f"找不到設定檔：{config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ScannerError(f"設定檔 JSON 格式錯誤：{exc}") from exc

    servers = cfg.get("nvr_servers", [])
    enabled = [s for s in servers if s.get("enabled", True)]
    if not enabled:
        raise ScannerError("設定檔中沒有任何 enabled NVR")
    # 解析 ${ENV_VAR} placeholder（2026-07-13：避免 password / username 明碼 commit）
    resolved = []
    for nvr in enabled:
        nvr2 = dict(nvr)
        for k in ("host", "username", "password"):
            if isinstance(nvr2.get(k), str) and "${" in nvr2[k]:
                nvr2[k] = substitute_env_vars(nvr2[k])
        resolved.append(nvr2)
    return {"scan_settings": cfg.get("scan_settings", {}), "nvr_servers": resolved}


def main() -> int:
    """
    CLI 入口：載入設定 → 讀取認證 → 呼叫 batch_scan 批次掃描 → 報表。

    Returns:
        0 = 成功；1 = 失敗（錯誤訊息已印至 stderr）。
    """
    # 區域 import 避免循環依賴（batch_scan 內部會 import nvr_scanner）
    from batch_scan import batch_scan

    config_path = Path(__file__).parent / CONFIG_FILENAME

    # --- 載入 .env 檔（不覆蓋已存在的 shell 環境變數） ---
    env_path = Path(__file__).parent / ".env"
    loaded = load_env_file(env_path)
    if loaded:
        print(f"[INFO] 已從 .env 載入 {loaded} 個環境變數")

    # --- 載入設定 ---
    try:
        cfg = _load_config(config_path)
    except ScannerError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    # --- 讀取認證材料（從環境變數或互動輸入） ---
    try:
        credentials = {
            "user_nonce": get_credential("AVIGILON_USER_NONCE", "請輸入 userNonce: "),
            "user_key": get_credential(
                "AVIGILON_USER_KEY", "請輸入 userKey: ", hide=True
            ),
            "integration_id": os.environ.get("AVIGILON_INTEGRATION_ID", ""),
            "username_override": os.environ.get("AVIGILON_USERNAME"),
            "password_override": os.environ.get("AVIGILON_PASSWORD"),
        }
    except (KeyboardInterrupt, EOFError):
        print("\n[FAIL] 認證輸入中斷", file=sys.stderr)
        return 1

    # --- 初始化 DB 寫入器 ---
    db_path = cfg["scan_settings"].get("db_path", "./nvr_scan.db")
    writer = SqliteWriter(db_path)
    print(f"[INFO] DB 路徑：{db_path}")
    print(f"[INFO] 目標 NVR 數：{len(cfg['nvr_servers'])}")

    # --- 跨 process 互斥：避免 cron + 手動同時跑 ---
    try:
        from db.sqlite_writer import acquire_scan_lock

        if not acquire_scan_lock(db_path, timeout=0):
            print(
                "[FAIL] 已有另一個 scan 在跑（DB 內 status='running'），放棄本次執行。",
                file=sys.stderr,
            )
            return 2
    except Exception as exc:
        print(f"[WARN] 無法檢查 scan lock: {exc}", file=sys.stderr)

    # --- 執行批次掃描 ---
    try:
        batch_scan(
            cfg,
            credentials,
            writer,
            timeout=cfg["scan_settings"].get("timeout_seconds", DEFAULT_TIMEOUT),
        )
        return 0
    except ScannerError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _now_utc_iso() -> str:
    """當下 UTC 時間，ISO 8601 格式（內部用，不導出）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    sys.exit(main())
