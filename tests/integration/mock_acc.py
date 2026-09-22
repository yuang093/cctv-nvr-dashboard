"""
tests/integration/mock_acc.py
=============================
MockAvigilonServer：模擬 Avigilon ACC 8.7+ Web Endpoint API 的 HTTPS server。

用於整合測試：真的網路傳輸 + 真的 session/SSL，但回應內容可控。

支援的端點（與 nvr_scanner.py 對齊）：
    POST /mt/api/rest/v1/login              → {status: success, result: {session}}
    GET  /mt/api/rest/v1/server/ids          → [{"serverId": "..."}]
    GET  /mt/api/rest/v1/cameras             → cameras list（with connectionStatus）
    GET  /mt/api/rest/v1/events/search       → events list

行為注入（per-NVR 配置）：
    - events   : list[dict] — 每筆事件含 eventId/deviceId/eventTopics/occurredAt
    - server_id: str | None — 若 None 改回傳空陣列（測試用）
    - login_behavior: "ok" | "fail" | "hang"
    - cameras_behavior: "ok" | "empty" | "hang"

使用範例：
    server = MockAvigilonServer(cameras=[...], events=[...])
    server.start()                        # 啟動在 127.0.0.1:0
    base_url = server.base_url            # e.g. https://127.0.0.1:54321
    # ... 用 AvigilonScanner 連 base_url
    server.stop()

SSL 憑證：每次啟動自動生成 self-signed cert（透過 openssl CLI），
          scanner 端設 verify_ssl=False 對齊真實 NVR 場景。
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


# === 預設 fixtures ===
def _default_cameras() -> list[dict]:
    """3 台相機：1 正常 + 1 LONG_FAILED + 1 DISCONNECTED。"""
    return [
        {
            "deviceId": "cam-001",
            "name": "前門",
            "logicalId": "L1",
            "connectionStatus": {"state": "CONNECTED"},
            "available": True,
        },
        {
            "deviceId": "cam-002",
            "name": "後門",
            "logicalId": "L2",
            "connectionStatus": {"state": "LONG_FAILED"},
            "available": False,
        },
        {
            "deviceId": "cam-003",
            "name": "倉庫",
            "logicalId": "L3",
            "connectionStatus": {"state": "DISCONNECTED"},
            "available": False,
        },
    ]


def _default_events() -> list[dict]:
    """2 異常事件：cam-001 VIDEO_LOSS + cam-002 TAMPERING。"""
    return [
        {
            "eventId": "evt-001",
            "deviceId": "cam-001",
            "eventTopics": ["DEVICE_VIDEO_SIGNAL_LOST"],
            "occurredAt": "2026-06-23T08:00:00Z",
        },
        {
            "eventId": "evt-002",
            "deviceId": "cam-002",
            "eventTopics": ["DEVICE_TAMPERING"],
            "occurredAt": "2026-06-23T08:05:00Z",
        },
    ]


def _all_normal_cameras(count: int = 3) -> list[dict]:
    """N 台全 CONNECTED 的相機（用於「全綠」情境）。"""
    return [
        {
            "deviceId": f"cam-{i:03d}",
            "name": f"相機{i}",
            "logicalId": f"L{i}",
            "connectionStatus": {"state": "CONNECTED"},
            "available": True,
        }
        for i in range(1, count + 1)
    ]


def _empty_events() -> list[dict]:
    """無事件（用於全綠 NVR）。"""
    return []


@dataclass
class MockAvigilonConfig:
    """單台 mock NVR 的狀態。"""

    server_id: str = "test-server-001"
    cameras: list[dict] = field(default_factory=_default_cameras)
    events: list[dict] = field(default_factory=_default_events)
    # 行為注入（測試用）
    login_behavior: str = "ok"  # "ok" / "fail"
    request_count: dict = field(default_factory=dict)  # 自動累加各端點呼叫次數
    request_log: list = field(default_factory=list)  # 自動 log 所有 request


# === 自簽憑證取得（優先用預先包好的 fixture，fallback 到 openssl）===
# Pre-bundled 自簽憑證位置（10 年效期，CN=127.0.0.1）
_FIXTURE_CERT = Path(__file__).resolve().parent.parent / "fixtures" / "cert.pem"
_FIXTURE_KEY = Path(__file__).resolve().parent.parent / "fixtures" / "key.pem"


def _generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """
    取得自簽憑證：優先複製預先包好的 fixture，fallback 才呼叫 openssl CLI。

    預先包憑證的目的：PowerShell / Windows 預設不帶 openssl.exe，
    這樣測試可以在沒有 openssl 的環境跑（CI 也方便）。

    Raises:
        RuntimeError: 既無 fixture 也無 openssl CLI。
    """
    # 1. 優先用 fixture
    if _FIXTURE_CERT.exists() and _FIXTURE_KEY.exists():
        cert_path.write_bytes(_FIXTURE_CERT.read_bytes())
        key_path.write_bytes(_FIXTURE_KEY.read_bytes())
        return

    # 2. Fallback：動態生成（需要 openssl CLI）
    cmd = [
        "openssl",
        "req",
        "-new",
        "-x509",
        "-days",
        "365",
        "-nodes",
        "-out",
        str(cert_path),
        "-keyout",
        str(key_path),
        "-subj",
        "/CN=127.0.0.1",
        "-addext",
        "subjectAltName=IP:127.0.0.1",
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "找不到 openssl CLI；也沒有預先包好的憑證 (tests/fixtures/cert.pem)。"
            "請執行 `openssl req -new -x509 -days 3650 -nodes "
            "-out tests/fixtures/cert.pem -keyout tests/fixtures/key.pem "
            '-subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1"` '
            "產生後再跑測試"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"openssl 失敗：{exc.stderr}") from exc


# === HTTPS request handler ===
class _MockAvigilonHandler(BaseHTTPRequestHandler):
    """依 path dispatch 到對應 ACC 端點行為。"""

    # 抑制 BaseHTTPRequestHandler 預設的 stderr log（測試期間吵）
    def log_message(self, format, *args):  # noqa: A002
        pass

    def _respond_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        cfg: MockAvigilonConfig = self.server.mock_config  # type: ignore[attr-defined]
        cfg.request_count["post"] = cfg.request_count.get("post", 0) + 1
        cfg.request_log.append(("POST", self.path))

        if self.path == "/mt/api/rest/v1/login":
            cfg.request_count["login"] = cfg.request_count.get("login", 0) + 1
            if cfg.login_behavior == "fail":
                self._respond_json(403, {"error": "denied"})
                return
            # 讀 body（login 也包含 credentials）
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            self._respond_json(
                200,
                {
                    "status": "success",
                    "result": {"session": f"sess-{cfg.server_id}"},
                },
            )
            return

        self._respond_json(404, {"error": "not found"})

    def do_GET(self):  # noqa: N802
        cfg: MockAvigilonConfig = self.server.mock_config  # type: ignore[attr-defined]
        cfg.request_count["get"] = cfg.request_count.get("get", 0) + 1
        cfg.request_log.append(("GET", self.path))

        # session 必須帶在 query string
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        if not query.startswith("session=sess-"):
            self._respond_json(401, {"error": "missing session"})
            return

        if self.path.startswith("/mt/api/rest/v1/server/ids"):
            cfg.request_count["server_ids"] = cfg.request_count.get("server_ids", 0) + 1
            self._respond_json(200, [{"serverId": cfg.server_id}])
            return

        if self.path.startswith("/mt/api/rest/v1/cameras"):
            cfg.request_count["cameras"] = cfg.request_count.get("cameras", 0) + 1
            self._respond_json(200, {"cameras": cfg.cameras})
            return

        if self.path.startswith("/mt/api/rest/v1/events/search"):
            cfg.request_count["events"] = cfg.request_count.get("events", 0) + 1
            self._respond_json(200, {"events": cfg.events})
            return

        self._respond_json(404, {"error": "not found"})


# === HTTPS Server ===
class MockAvigilonServer:
    """
    單台 mock NVR。模擬 ACC 8.7 API。

    使用：
        server = MockAvigilonServer()
        server.start()
        # server.base_url  # https://127.0.0.1:PORT
        # server.config.cameras / events 動態讀寫
        server.stop()
    """

    def __init__(
        self,
        config: MockAvigilonConfig | None = None,
        *,
        host: str = "127.0.0.1",
    ):
        """
        Args:
            config: mock NVR 設定（None 時用 default）。
            host: 綁定 IP（預設 127.0.0.1，與 ACC 預設 8443 不同以避免衝突）。
        """
        self.config = config or MockAvigilonConfig()
        self.host = host
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._cert_dir: tempfile.TemporaryDirectory | None = None
        self.port: int = 0

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"

    def start(self) -> None:
        """啟動 mock server（在子執行緒跑 HTTPS listener）。"""
        # 1. 生 self-signed cert
        self._cert_dir = tempfile.TemporaryDirectory(prefix="mock_acc_")
        cert_path = Path(self._cert_dir.name) / "cert.pem"
        key_path = Path(self._cert_dir.name) / "key.pem"
        _generate_self_signed_cert(cert_path, key_path)

        # 2. 建 SSLContext
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._ssl_context = ctx

        # 3. 建 HTTPServer（綁 0 = 系統分派 port）
        httpd = HTTPServer((self.host, 0), _MockAvigilonHandler)
        # 讓 stop() 之後新 server 可立刻重用 port（避免 TIME_WAIT 卡住新測試）
        httpd.socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )
        httpd.mock_config = self.config  # type: ignore[attr-defined]
        httpd.socket = ctx.wrap_socket(
            httpd.socket,
            server_side=True,
        )
        self._httpd = httpd
        self.port = httpd.server_address[1]

        # 4. 在子 thread 跑 serve_forever()
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            daemon=True,
        )
        self._thread.start()

        # 5. 等 server 真的起來（避免 race）
        # 連到 HTTPS port 並確認能完成 TLS handshake（最快 50ms × 多輪）
        # socket.create_connection 只確認 TCP 層；真正能服務須等 SSL handshake
        deadline = time.time() + 5.0
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                # 嘗試完整 TLS handshake（繞過 verify 才能測自簽）
                with socket.create_connection(
                    (self.host, self.port),
                    timeout=0.5,
                ) as raw_sock:
                    with ctx.wrap_socket(raw_sock, server_hostname=self.host) as ssock:
                        ssock.send(b"GET / HTTP/1.0\r\n\r\n")
                        ssock.recv(16)  # 只要 server 開始回應即可
                        return
            except (OSError, ssl.SSLError) as exc:
                last_err = exc
                time.sleep(0.05)
        raise RuntimeError(
            f"MockAvigilonServer 啟動失敗（{type(last_err).__name__}: {last_err}）"
        )

    def stop(self) -> None:
        """停掉 server。"""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._ssl_context is not None:
            self._ssl_context = None
        if self._cert_dir is not None:
            self._cert_dir.cleanup()
            self._cert_dir = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


# === 工廠函式：常見 mock NVR 場景 ===


def make_normal_nvr(camera_count: int = 3) -> MockAvigilonConfig:
    """N 台相機、零事件 → 預期 0 異常。"""
    return MockAvigilonConfig(
        server_id="mock-normal",
        cameras=_all_normal_cameras(camera_count),
        events=_empty_events(),
    )


def make_abnormal_nvr() -> MockAvigilonConfig:
    """預設 fixture：2 異常 + 1 正常 + 2 異常事件。"""
    return MockAvigilonConfig(server_id="mock-abnormal")


def make_login_fail_nvr() -> MockAvigilonConfig:
    """login 故意回 403 → 預期 batch 標記此台失敗。"""
    return MockAvigilonConfig(
        server_id="mock-loginfail",
        login_behavior="fail",
        cameras=[],
        events=[],
    )


def _quick_smoke() -> None:
    """本檔獨立測試：跑 `python -m tests.integration.mock_acc` 簡單 smoke。"""
    from urllib.request import Request, urlopen
    import ssl

    with MockAvigilonServer() as s:
        print(f"[INFO] base_url={s.base_url}")
        # 真的打 /login
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        body = json.dumps({"username": "u", "password": "p"}).encode("utf-8")
        req = Request(
            s.base_url + "/mt/api/rest/v1/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, context=ctx, timeout=2) as resp:  # noqa: S310
            print(f"[INFO] login resp: {resp.read()!r}")
        print(f"[INFO] request_count: {s.config.request_count}")


# === Webhook 接收器（測試 webhook 模組用）===
class MockWebhookReceiver:
    """
    接收 webhook POST 的 HTTP server（純 HTTP，不需要 SSL）。

    啟動後 collector 在 .received list 累加每筆收到的（path, headers, body, decoded_json）。
    可設定 response_status（預設 200）與 response_body。
    """

    def __init__(
        self,
        *,
        response_status: int = 200,
        response_body: str = "ok",
    ):
        self.response_status = response_status
        self.response_body = response_body
        self.received: list[dict] = []  # 所有接收的記錄
        self._httpd: HTTPServer | None = None  # Week 7 Task 7: 加 Optional 型別
        self._thread: threading.Thread | None = None  # Week 7 Task 7: 加 Optional 型別
        self.port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        receiver = self  # closure

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(length) if length else b""
                try:
                    decoded = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    decoded = None
                receiver.received.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body_bytes": body_bytes,
                        "decoded": decoded,
                    }
                )
                body = receiver.response_body.encode("utf-8")
                self.send_response(receiver.response_status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self._httpd = httpd
        self.port = httpd.server_address[1]

        self._thread = threading.Thread(
            target=httpd.serve_forever,
            daemon=True,
        )
        self._thread.start()

        # 等 socket 可連
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


if __name__ == "__main__":
    _quick_smoke()
