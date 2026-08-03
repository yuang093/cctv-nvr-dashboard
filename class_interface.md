# AvigilonScanner 介面規格 (Class Interface Contract)

> 本文件定義 `AvigilonScanner` 及相關介面的對外契約。
> 修改任何介面前請先讀本文件；實作後請同步更新本文件。

---

## 設計原則

- **單一職責**：每個 scanner instance 只負責一台 NVR。
- **無狀態寫入**：scanner 本身不直接寫 DB；呼叫端把結果交給 `SqliteWriter`。
- **明確錯誤**：所有失敗以自訂例外回傳，不靜默吞錯。
- **可注入**：scanner 的 `requests.Session` 可注入（測試用）；DB writer 採依賴注入。

---

## `class AvigilonScanner`

### 建構子

```python
def __init__(
    self,
    nvr_config: dict,
    *,
    # === Avigilon 開發者金鑰（必填，規格見 Avigilon_API_Reference.md §1）===
    user_nonce: str,
    user_key: str,
    integration_id: str = "",
    # === 客戶端識別 ===
    client_name: str = "PythonScannerApp",
    client_version: str = "8.7.3.4",      # ACC 版本對齊（從 /health?v 取）
    # === HTTP 行為 ===
    timeout: int = 10,                     # HTTP 逾時秒數
    verify_ssl: bool = False,              # NVR 多為自簽，預設 False
    page_size: int = 100,                  # cameras / events 單頁大小
    session: requests.Session | None = None,  # 可注入 mock session（測試用）
):
    ...
```

> **2026-06-23 介面變更**：新增 `user_nonce` / `user_key` / `integration_id` / `client_name` / `client_version`。
> 原因：實測發現 ACC 8.7+ Web Endpoint 必須帶 SHA-256 `authorizationToken`，否則回 `403 Unknown reason`。
> 完整規格：`Avigilon_API_Reference.md §1`。

### 主要方法

```python
def login(self) -> str:
    """
    POST /mt/api/rest/v1/login，回傳 session token。
    內部自動計算 SHA-256 authorizationToken。
    失敗拋 AuthError。
    """

def get_server_ids(self) -> str:
    """
    GET /mt/api/rest/v1/server/ids，回傳 serverId。
    內部快取於 self._server_id，後續 cameras / events 會複用。
    """

def get_cameras(self) -> dict[str, dict]:
    """
    GET /mt/api/rest/v1/cameras，回傳豐富的相機資訊。
    Return: {deviceId: {"name": str, "connection_state": str, "available": bool}}
    """

def get_active_events(self) -> list[dict]:
    """
    GET /mt/api/rest/v1/events/search?queryType=ACTIVE，回傳 ACTIVE 事件清單。
    不過濾，回傳原始格式。
    """

def scan(self) -> dict:
    """
    完整掃描一台 NVR，回傳結構化結果。

    內部流程：login → get_server_ids → get_cameras → get_active_events → 過濾。
    不寫 DB；呼叫端決定是否呼叫 writer 操作。

    異常判定採**雙重檢查**（任一符合即標異常）：
        1. ACTIVE events 匹配 ABNORMAL_KEYWORDS
        2. cameras[].connectionStatus.state != "CONNECTED"（防 ACC events 未生成）

    Returns:
        {
            "nvr_id": str,
            "nvr_name": str,
            "cameras": {deviceId: {"name": ..., "connection_state": ..., "available": ...}},
            "events": [abnormal events（含來自 connection_status 合成的事件），去重後],
            "stats": {"total_cameras": int, "abnormal_cameras": int},
        }
    """
```

> **2026-06-29 介面變更**：`get_server_id` → `get_server_ids`（複數），回傳單一 serverId 字串但 method 名反映 API 端點（`/server/ids`）。

### 內部常數

```python
DEFAULT_ABNORMAL_KEYWORDS = (
    "DEVICE_VIDEO_SIGNAL_LOST",     # 影像訊號斷線
    "DEVICE_TAMPERING",             # 破壞 / 遮蔽
    "DEVICE_COMMUNICATION_LOST",    # 通訊中斷（防 events 沒生）
    "DEVICE_CONNECTION_ERROR",      # 連線錯誤
    "DEVICE_LONG_FAILED",           # 長期失敗（拔網路線）
    "DEVICE_DISCONNECTED",          # 斷線
    "DEVICE_ANOMALY_START",         # 影像分析異常
    "DEVICE_UNUSUAL_STARTED",       # 未預期活動
)
```

> **2026-06-23 變更**：從泛用的 `VIDEO_LOSS` / `TAMPER` 換成 ACC 8.7 實測的 `DEVICE_*` 主題（由 `discover_event_subtopics.py` 探勘 229 個 DEVICE_* 主題確認）。

### 例外類別

```python
class ScannerError(Exception):
    """AvigilonScanner 所有錯誤的根例外。"""

class AuthError(ScannerError):
    """認證 / 授權失敗（HTTP 401/403、token 缺值、密碼錯等）。"""

class ConnectionError_(ScannerError):
    """網路層錯誤（timeout / SSL / 連線失敗）。命名避開內建 ConnectionError。"""

class ApiResponseError(ScannerError):
    """API 回應格式錯誤或非預期狀態碼。"""
```

> 註：內建 `ConnectionError` 同名會衝突，故底線為 placeholder。亦可改為 `NetworkError`。

---

## 批次入口：`batch_scan()`

Scanner 本體只處理單機，但需要一個**協調者**來批次處理多台 NVR。實作於 `batch_scan.py`：

```python
def batch_scan(
    config: dict,            # _load_config() 回傳值（含 nvr_servers 清單，已過濾 enabled）
    credentials: dict,       # {user_nonce, user_key, integration_id, username_override, password_override}
    writer: SqliteWriter,    # 已 init schema 的 DB 寫入器
    *,
    timeout: int = 10,
    verbose: bool = True,
) -> dict:
    """
    流程：
        1. 套用帳密環境變數覆寫（套到每台 NVR）
        2. 預先 upsert 所有 NVR 到 nvr_servers 表（即使 scan 失敗設定也保留）
        3. 開啟單一 batch scan_run transaction
        4. 對每台 NVR 建立 AvigilonScanner instance（per-NVR，不共用 session）
        5. 呼叫 scan() + writer.upsert_cameras/insert_events
        6. 失敗的 NVR try/except 吞掉，繼續下一台（不中斷整批）
        7. 全部完成後 finish_scan_run 並 commit transaction
        8. 回傳彙總結果

    status 規則：
        - 全部成功 → 'success'
        - 部分成功 → 'partial'
        - 全部失敗 → 'failed'

    Returns:
        {
            'scan_run_id': int,
            'total_nvrs' / 'ok_nvrs' / 'failed_nvrs': int,
            'total_cameras' / 'abnormal_cameras': int,
            'status': str,
            'started_at' / 'finished_at': str (ISO 8601),
            'per_nvr_results': [...],
            'failures': [{'nvr_id', 'nvr_name', 'type', 'error'}, ...],
        }

    Raises:
        ScannerError: config['nvr_servers'] 為空
    """
```

---

## 資料寫入介面：`IDatabaseWriter`

```python
from typing import Protocol

class IDatabaseWriter(Protocol):
    """資料寫入層抽象，方便測試時替換為 in-memory mock。"""

    def upsert_nvr(self, nvr_config: dict) -> int:
        """新增或更新 nvr_servers 表記錄，回傳內部主鍵 ID。"""

    def begin_scan_run(self, started_at: str) -> int:
        """建立一筆 scan_runs，回傳 scan_run_id。"""

    def upsert_cameras(self, nvr_id: int, cameras: dict) -> None:
        """將 cameras 寫入 / 更新 cameras 表（同一 nvr_id+device_id 不重複）。"""

    def insert_events(self, scan_run_id: int, nvr_id: int, events: list[dict]) -> None:
        """寫入 events 表。"""

    def finish_scan_run(
        self,
        scan_run_id: int,
        *,
        finished_at: str,
        status: str,
        stats: dict,
    ) -> None:
        """結束 scan_run，commit transaction。stats 支援 keys：
            - total_cameras / abnormal_cameras
            - total_nvrs / ok_nvrs / failed_nvrs
        """
```

預設實作：`SqliteWriter(db_path: str)`，於 `db/sqlite_writer.py`。

### `SqliteWriter` 擴充方法（超出 IDatabaseWriter Protocol）

```python
def get_scan_runs(self, limit: int = 10) -> list[dict]:
    """查詢最近 N 次 scan_run（除錯 / Web UI 用）。"""

def get_events_for_run(self, scan_run_id: int) -> list[dict]:
    """查詢某次掃描的所有事件，JOIN cameras 解析 camera_name。"""

def mark_resolved(
    self,
    scan_run_id: int,
    nvr_id: int,
    *,
    resolved_at: str | None = None,
) -> int:
    """Phase 1：把此 NVR「這次 scan 沒再出現」的先前 OPEN 事件標記為已解決。

    邏輯：對該 NVR 所有 resolved_at IS NULL 的 prior events，
          若 device_id 也在這次 scan_run 內有 event → 視為仍異常（不動）；
          若 device_id 這次 scan_run 沒 event → resolved_at = NOW()。

    Args:
        scan_run_id: 本次 scan_run ID（必為 active）。
        nvr_id: 本 NVR 的內部 ID。
        resolved_at: 自訂時間（UTC ISO 8601 字串）；None = NOW()。

    Returns:
        被更新的 row 數。
    """
```

```python
def log_nvr_failure(
    self,
    scan_run_id: int,
    nvr_id: str,
    nvr_name: str,
    error_type: str,
    error_message: str,
    *,
    nvr_internal_id: int | None = None,
    failed_at: str | None = None,
) -> int:
    """Phase 2.7+：記錄單台 NVR 連線失敗（給 dashboard / run_detail 個別顯示）。

    Args:
        scan_run_id: 本次 scan_run ID（必為 active）。
        nvr_id: NVR 設定檔的 id 字串（例 "NVR-A"）。
        nvr_name: 顯示名稱（snapshot，nvr_config 改了不影響歷史）。
        error_type: 例 "ConnectionError" / "Timeout" / "AuthError"（=exc 類別名）。
        error_message: 完整錯誤訊息。
        nvr_internal_id: nvr_servers.id（upsert 失敗時為 None）。
        failed_at: 自訂時間（UTC ISO 8601 字串）；None = NOW()。

    Returns:
        新插入 row 的 id。

    Note:
        呼叫時機：batch_scan 的 except handler 內、commit transaction 之前。
        不會主動 commit；由 finish_scan_run 的 commit 一起持久化。
    """
```

### Transaction 語意

- 一個 `SqliteWriter` instance 同時只維護一個 active scan_run transaction。
- `begin_scan_run()` → 開啟 transaction（不 commit）。
- `upsert_cameras()` / `insert_events()` / `mark_resolved()` → 操作 transaction 內資料。
- `mark_resolved()` 必須在 `insert_events()` 之後、`finish_scan_run()` 之前呼叫（要在同一 transaction commit）。
- `finish_scan_run()` → **commit** + 結束 transaction（連線保持開啟以支援 `:memory:` 模式）。
- **未 finish 就 close connection → 自動 rollback**（uncommitted 資料消失）。

呼叫端不需手動管理 transaction，只要嚴守 `begin → 操作 → finish` 順序即可。

---

## NVR 設定查詢／切換介面（v2.7+，`web/db.py`）

> 來源：`web/db.py`（2026-07-07 起，背景掃描改從 DB 讀 NVR，DB 為單一 source of truth）。
> 範圍：給 `batch_scan`、Web UI 用；不寫入 NVR 設定以外的資料。

### `list_enabled_nvrs(db_path) -> list[dict]`

```python
def list_enabled_nvrs(db_path: str) -> list[dict]:
    """從 DB 撈所有 enabled=1 的 NVR，轉成 batch_scan 預期的 dict 格式。

    Returns:
        每個 dict 含 keys：
        - id              (str)  NVR 設定檔的 id（例 "NVR-A"）
        - name            (str)
        - host            (str)  IP / hostname
        - port            (int)  HTTPS port（預設 8443）
        - username        (str)
        - password        (str)  ⚠️ 明碼；只在 worker 內使用
        - verify_ssl      (bool) 預設 False（自簽）
        - site_id         (str | None)
        - tags            (list[str])
        - nvr_internal_id (int)   nvr_servers.id（給 log_nvr_failure 用）

    Note:
        batch_scan.main() 與 web._run_scan_in_background 都呼叫此函式；
        若 DB 為空，batch_scan 會 fallback 從 nvr_config.json seed 一次。
    """
```

### `set_nvr_enabled(db_path, internal_id, enabled) -> bool`

```python
def set_nvr_enabled(db_path: str, internal_id: int, enabled: bool) -> bool:
    """切換單台 NVR 啟用狀態（不刪資料）。

    Args:
        db_path: DB 檔路徑。
        internal_id: nvr_servers.id（內部整數主鍵）。
        enabled: True=啟用、False=停用。

    Returns:
        True=有更新到一筆；False=找不到該 internal_id。

    Note:
        對應 Web route `POST /nvrs/<int:internal_id>/toggle`。
        停用後 batch_scan 不會掃此 NVR；再啟用後下次掃描立刻納入。
    """
```

---

## 影片片段介面：`MediaApiClient`（Phase 2.7）

> 來源：`web/clip_retrieval.py`（2026-07-06）。
> 對應 NVR Media API（`/mt/api/rest/v1/media`，port 8443）。
> 給另一部門調閱 30 秒影片片段用；不修改 worker 既有流程。

### 設計原則

- **Protocol-only 抽象**：3 個 method（`get_snapshot` / `get_mpd_manifest` / `fetch_clip`），**不負責登入**；session token 由呼叫端注入。
- **可注入實作**：`MpdMediaClient`（真實 NVR，佔位實作）+ `MockMediaClient`（測試用回 fixture）。
- **依賴方向**：`web.clips_app` → `MediaApiClient`（注入測試）→ `MockMediaClient` / `MpdMediaClient`。

### Protocol 定義

```python
from typing import Iterator, Protocol, runtime_checkable
from datetime import datetime

@runtime_checkable
class MediaApiClient(Protocol):
    """對應 NVR Media API（/mt/api/rest/v1/media）。"""

    def get_snapshot(
        self,
        camera_id: str,
        at_time: datetime,    # UTC；NVR 用 ISO 8601 compact 格式
    ) -> bytes:
        """GET ?format=jpeg&t=<time> → 一張 JPEG bytes。"""

    def get_mpd_manifest(
        self,
        camera_id: str,
    ) -> str:
        """GET ?format=mpd → XML MPD 字串。"""

    def fetch_clip(
        self,
        camera_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[bytes]:
        """30 秒 clip 串流（fragmented MP4 bytes）。"""
```

### `class MockMediaClient`

| 屬性 / 用途 | 說明 |
|---|---|
| Constructor 參數 | `snapshot_bytes: int`、`mpd_xml: str`、`clip_chunk_size: int`、`clip_chunks: int` |
| `snapshot_calls` | list，記錄所有 `get_snapshot` 呼叫（camera_id, at_time）|
| `manifest_calls` | list，記錄所有 `get_mpd_manifest` 呼叫（camera_id）|
| `clip_calls` | list，記錄所有 `fetch_clip` 呼叫（camera_id, start, end）|

測試可用這些屬性驗證呼叫次數 / 參數。

### `class MpdMediaClient`（佔位實作，待 NVR 上線驗證）

| 屬性 | 說明 |
|---|---|
| `MEDIA_PATH` | `"/mt/api/rest/v1/media"` |
| `SUPPORTED_FORMATS` | `("mpd", "fmp4", "jpeg", "json", "webm", "spkc")` |
| Constructor | `(host, port=8443, session, verify_ssl=False)` |
| 3 個 method | **目前 raise NotImplementedError**；spec 見 `docs/media-api-research.md` §2 |

預期完整實作 ~30 行 requests.Session.get + xml.etree parse。

### 變更紀律

任何修改 `MediaApiClient` 介面的 PR 必須：
1. 更新本文件
2. 更新 `web/clip_retrieval.py` Protocol 定義
3. 同步更新 `MockMediaClient` 測試斷言
4. 跑 `pytest -q` 確認 265 項全綠

---

## 整合測試介面：`MockAvigilonServer`

> 來源：`tests/integration/mock_acc.py`（2026-06-29）。
> 用於整合測試：真的 HTTPS 傳輸 + 真的 session/SSL，但回應內容可控。

### `class MockAvigilonConfig`

單台 mock NVR 的狀態（cameras / events / 行為注入）：

```python
@dataclass
class MockAvigilonConfig:
    server_id: str = "test-server-001"
    cameras: list[dict] = field(default_factory=_default_cameras)
    events: list[dict] = field(default_factory=_default_events)
    login_behavior: str = "ok"           # "ok" | "fail"
    # 自動累加（測試驗證用）
    request_count: dict = field(default_factory=dict)
    request_log: list = field(default_factory=list)
```

預設 fixtures（用於測試）：
- `_default_cameras()`：3 台相機（1 CONNECTED + 1 LONG_FAILED + 1 DISCONNECTED）
- `_default_events()`：2 異常事件（DEVICE_VIDEO_SIGNAL_LOST + DEVICE_TAMPERING）
- `make_normal_nvr(count)`：N 台全 CONNECTED
- `make_login_fail_nvr()`：login 回 403

### `class MockAvigilonServer`

```python
class MockAvigilonServer:
    def __init__(
        self,
        config: MockAvigilonConfig | None = None,
        *,
        host: str = "127.0.0.1",
    ): ...

    @property
    def base_url(self) -> str:
        """https://<host>:<port>（port 由系統分派，start() 之後有效）。"""

    def start(self) -> None:
        """啟動 HTTPS server（生成自簽憑證 + threading serve_forever）。"""

    def stop(self) -> None:
        """停掉 server，釋放 port 與憑證檔案。"""

    def __enter__(self) -> "MockAvigilonServer": ...
    def __exit__(self, *exc_info) -> None: ...
```

### Mock 端點行為

| Method | Path | 回應 |
|---|---|---|
| POST | `/mt/api/rest/v1/login` | 200 `{status: success, result: {session}}` 或 403（fail 模式） |
| GET | `/mt/api/rest/v1/server/ids` | 200 `[{serverId}]`（需帶 `?session=`） |
| GET | `/mt/api/rest/v1/cameras` | 200 `{cameras: [...]}`（需帶 `?session=`） |
| GET | `/mt/api/rest/v1/events/search` | 200 `{events: [...]}`（需帶 `?session=`） |

### 整合測試 fixtures（`tests/integration/conftest.py`）

| Fixture | 用途 |
|---|---|
| `mock_nvr_server` | 單台含異常事件 + 異常 state |
| `mock_nvr_normal` | 單台全正常 |
| `mock_nvr_login_fail` | 單台 login 失敗 |
| `multi_nvr_servers` | 3 台（normal / abnormal / login-fail） |
| `integration_config` | 對應 `multi_nvr_servers` 的 batch_scan config |
| `integration_credentials` | 完整 credentials dict |
| `integration_db` | tempfile 檔案 DB（worker + web 跨連線） |

---

## 測試策略

- **單元測試**：以 mock `requests.Session`（`conftest.py::mock_session`）注入 fixtures，驗證各方法。
- **整合測試**：用 `MockAvigilonServer` 跑完整 `scan()` / `batch_scan()` / Flask 渲染。
- **契約測試**：以 `typing.Protocol` 靜態檢查 `IDatabaseWriter` 實作完整性（隱式，pytest 不獨立跑）。

### 測試分布

| 檔案 | 項數 | 範圍 |
|---|---|---|
| `tests/test_helpers.py` | 23 | SHA-256 auth、env、unwrap、_extract_list |
| `tests/test_scanner.py` | 15 | AvigilonScanner 各方法 |
| `tests/test_sqlite_writer.py` | 11 | 寫入層 lifecycle / JOIN / 連續 / rollback |
| `tests/test_batch_scan.py` | 9 | batch_scan 狀態規則 / failures / env 覆寫 |
| `tests/test_web.py` | 11 | Web 5 路由 + 404 + static |
| `tests/integration/test_e2e_single_nvr.py` | 8 | mock HTTPS ↔ scanner |
| `tests/integration/test_e2e_batch_scan.py` | 8 | 3 NVR partial / DB 交易 |
| `tests/integration/test_e2e_web.py` | 11 | Flask 渲染 + 真實跨連線 |
| **合計** | **96** | — |

---

## 變更紀律

任何修改介面的 PR 必須：
1. 更新本文件
2. 更新 `database_schema.md`（若涉及資料流）
3. 更新 mock fixtures（若有）
4. 更新既有測試
5. 跑 `pytest -q` 確認 96 項全綠
