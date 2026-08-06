"""
test_clips_fetch_error_handling.py
==================================
2026-07-14 fix07.txt：
  user 回報 8555 同步撥放時出現「Unexpected token '<', "<!doctype"... is not valid JSON」
  兩台攝影機都掛掉（📭 圖示）。

Root cause：
  web/clips_app.py 的 /clips/fetch endpoint 把 client.fetch_clip() 包進
  stream_with_context(...)。fetch_clip 是 generator，在第一次 yield 之前若
  raise RuntimeError（例如 NVR 回 500），Flask 把它當未捕獲例外，回 **HTML 500**
  error page。clips.html:425 的 r.json() 收到 HTML → JSON parse fail。

驗證項目：
  1. NVR fetch_clip 失敗時，/clips/fetch 回 application/json（不是 text/html）
  2. JSON 內含「NVR 端錯誤」字串，前端能 echo 給 user 看
  3. 0-byte body 也回 JSON（不是空 video）
  4. 正常路徑仍能跑（regression check）
"""
from __future__ import annotations

import gc
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# 確保 import clips_app 前 env vars 已設
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")

from db.sqlite_writer import SqliteWriter  # noqa: E402
from web.clips_app import _SessionStore, app  # noqa: E402


@pytest.fixture
def seeded_app_for_fetch(monkeypatch, tmp_path):
    """建立灌好測試資料的 DB + Flask app。"""
    db_path = str(tmp_path / "fetch_err_test.db")
    monkeypatch.setenv("NVR_DB_PATH", db_path)

    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "NVR-ERR-A", "name": "Fetch error test NVR", "host": "10.0.0.99",
        "port": 8443, "username": "u", "password": "p", "tags": [],
    })
    w.begin_scan_run("2026-07-14T00:00:00Z")
    w.upsert_cameras(1, {
        "cam-ok": {"name": "正常 cam", "connection_state": "CONNECTED", "available": True},
        "cam-bad": {"name": "會失敗 cam", "connection_state": "CONNECTED", "available": True},
    })
    w.finish_scan_run(
        1, finished_at="2026-07-14T00:00:30Z", status="success",
        stats={"total_cameras": 2, "abnormal_cameras": 0,
               "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0},
    )
    del w
    gc.collect()

    app.config["SESSION_STORE"] = _SessionStore()
    app.config["TESTING"] = True
    yield app, db_path


class _BoomClient:
    """Mock client：fetch_clip 立刻 raise RuntimeError（修法前的舊行為）。

    2026-07-14 後改用三種新 exception type（NvrNoRecordingError / Auth / Internal）
    區分錯誤類型，但 fallback 仍支援舊的 generic RuntimeError。
    """

    def __init__(self, *, error_msg: str = "NVR 模擬 500") -> None:
        self.error_msg = error_msg

    def get_snapshot(self, camera_id, at_time):
        return b"\x00" * 1024

    def get_mpd_manifest(self, camera_id, at_time="live"):
        return "<MPD/>"

    def get_recording_duration(self, camera_id, at_time):
        return 30.0  # 讓擴搜邏輯以為有錄影

    def fetch_clip(self, camera_id, start_time, end_time, target_seconds=None, max_wall_seconds=None):
        # 模擬 NVR fmp4 endpoint 在 iterator 起始就拋 generic RuntimeError
        # 對應舊版的行為；現版本會被歸類成 NVR_INTERNAL_ERROR
        raise RuntimeError(self.error_msg)


class _NoRecordingClient:
    """2026-07-14：模擬 NVR fmp4 回 404 → 該時段無錄影。"""

    def get_snapshot(self, camera_id, at_time):
        return b"\x00" * 1024

    def get_mpd_manifest(self, camera_id, at_time="live"):
        return "<MPD/>"

    def get_recording_duration(self, camera_id, at_time):
        return 0.0  # duration ≈ 0 → NO_RECORDING 觸發在 clips_app.py

    def fetch_clip(self, camera_id, start_time, end_time, target_seconds=None, max_wall_seconds=None):
        from web.clip_retrieval import NvrNoRecordingError
        raise NvrNoRecordingError("NVR 找不到此時段錄影（404）")


class _AuthFailedClient:
    """2026-07-14：模擬 NVR fmp4 回 401 → auth 過期。"""

    def get_snapshot(self, camera_id, at_time):
        return b"\x00" * 1024

    def get_mpd_manifest(self, camera_id, at_time="live"):
        return "<MPD/>"

    def get_recording_duration(self, camera_id, at_time):
        return 30.0

    def fetch_clip(self, camera_id, start_time, end_time, target_seconds=None, max_wall_seconds=None):
        from web.clip_retrieval import NvrAuthError
        raise NvrAuthError("NVR 認證失敗（401）")


class _NvrInternalClient:
    """2026-07-14：模擬 NVR fmp4 回 500。"""

    def get_snapshot(self, camera_id, at_time):
        return b"\x00" * 1024

    def get_mpd_manifest(self, camera_id, at_time="live"):
        return "<MPD/>"

    def get_recording_duration(self, camera_id, at_time):
        return 30.0

    def fetch_clip(self, camera_id, start_time, end_time, target_seconds=None, max_wall_seconds=None):
        from web.clip_retrieval import NvrInternalError
        raise NvrInternalError("NVR 內部錯誤（500）：{\"meta\":{\"code\":-1}}")


def _fake_session_token(nvr_row):
    """模擬 session token（給 get_session_for_nvr monkeypatch 用）。"""
    return f"FAKE-SESSION-{nvr_row['internal_id']}"


def test_nvr_500_returns_json_not_html(seeded_app_for_fetch, monkeypatch):
    """核心 regression 測試：NVR fetch_clip raise 時，
    /clips/fetch 必須回 application/json + 502，不能回 HTML。"""
    flask_app, db_path = seeded_app_for_fetch

    # Monkeypatch 掉 get_session_for_nvr / get_client_for_nvr，使用我們的 BoomClient
    import web.clips_app as ca
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda nvr_id, store: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr",
        lambda nvr_row, session_token: _BoomClient(
            error_msg="NVR fetch_clip 失敗 HTTP 500：內部錯誤",
        ),
    )
    # 設成非 mock 模式，才走真 client factory
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-fake-boom")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-bad",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)

    # 必須是 JSON，不是 HTML（這是 fix07.txt 的關鍵）
    ctype = resp.headers.get("Content-Type", "")
    assert "application/json" in ctype, (
        f"NVR 失敗時必須回 JSON，但 Content-Type={ctype!r}（這就是 fix07.txt 問題）"
    )
    assert resp.status_code == 502, (
        f"預期 502 (Bad Gateway 表示 NVR 端故障)，實際 {resp.status_code}"
    )

    body = resp.get_json()
    assert body is not None, "JSON body 應可被解析"
    # 2026-07-14 後：generic RuntimeError fallback 為 NVR_INTERNAL_ERROR
    assert body["error"] == "NVR_INTERNAL_ERROR", (
        f"預期 NVR_INTERNAL_ERROR 錯誤代碼，實際：{body.get('error')!r}"
    )
    # 應帶 stage + camera_id 讓前端 echo
    assert "stage" in body
    assert body.get("camera_id") == "cam-bad"


def test_nvr_500_body_does_not_contain_doctype(seeded_app_for_fetch, monkeypatch):
    """防止回退到 Flask HTML error page（如果修了又被改回去）。"""
    flask_app, db_path = seeded_app_for_fetch
    import web.clips_app as ca
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr",
        lambda nvr_row, session_token: _BoomClient(),
    )
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-fake-boom")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-bad",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)
    raw = resp.get_data(as_text=True)
    # HTML doctype 不該出現在 response
    assert "<!doctype" not in raw.lower() and "<!DOCTYPE" not in raw, (
        "response 不應包含 HTML doctype"
    )
    # JSON 開頭不該是 < （JSON 開頭通常是 { 或 [）
    stripped = raw.lstrip()
    assert not stripped.startswith("<"), (
        f"response 不應以 < 開頭（會讓前端 r.json() 失敗）；實際開頭：{raw[:80]!r}"
    )


def test_normal_path_still_works(seeded_app_for_fetch, monkeypatch):
    """Regression：正常 mock client 仍能回 video/mp4。"""
    flask_app, db_path = seeded_app_for_fetch
    # 保持 NVR_CLIPS_CLIENT=mock → clips_app.py 自己會用 MockMediaClient
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "mock")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-ok",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)
    assert resp.status_code == 200
    ctype = resp.headers.get("Content-Type", "")
    assert "video/mp4" in ctype, f"正常路徑應回 video/mp4，實際 {ctype!r}"
    # X-Actual-Duration 等 header 應保留
    assert resp.headers.get("X-Actual-Duration")
    assert resp.headers.get("X-Actual-Start")
    assert resp.headers.get("Content-Length")  # 新修法加的


def test_empty_body_returns_404_json(seeded_app_for_fetch, monkeypatch):
    """NVR fetch_clip 回 0 bytes 應回 JSON 404（不是空的 MP4）。"""

    class _EmptyClient(_BoomClient):
        def fetch_clip(self, camera_id, start_time, end_time, target_seconds=None, max_wall_seconds=None):
            return iter([])  # 空 generator

    flask_app, db_path = seeded_app_for_fetch
    import web.clips_app as ca
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr",
        lambda nvr_row, session_token: _EmptyClient(),
    )
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-fake-empty")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-bad",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)
    ctype = resp.headers.get("Content-Type", "")
    assert "application/json" in ctype
    assert resp.status_code == 404
    body = resp.get_json()
    assert body.get("error") == "EMPTY_CLIP"


# ==============================================================================
# 2026-07-14：細分 NVR 錯誤類型 → 不同前端顯示
# ==============================================================================


def test_nvr_404_returns_no_recording_json(seeded_app_for_fetch, monkeypatch):
    """NVR fmp4 回 404 → 前端友善顯示「此時段無錄影資料」。

    修復前：404 會被當通用 RuntimeError → 502 + NVR_INTERNAL_ERROR → 紅框 ⚠️
    修復後：404 應轉成 NO_RECORDING → 404 + error: NO_RECORDING → 灰色友善訊息
    """
    flask_app, db_path = seeded_app_for_fetch
    import web.clips_app as ca
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr",
        lambda nvr_row, session_token: _NoRecordingClient(),
    )
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-fake-404")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-bad",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)
    ctype = resp.headers.get("Content-Type", "")
    assert "application/json" in ctype
    assert resp.status_code == 404, f"404 應對應 NO_RECORDING → 404 status code"
    body = resp.get_json()
    assert body["error"] == "NO_RECORDING", (
        f"前端會用 errorCode === 'NO_RECORDING' 顯示友善訊息，"
        f"但 server 回了 {body.get('error')!r}"
    )
    assert "此時段無錄影" in body.get("message", "")


def test_nvr_401_returns_auth_failed_json(seeded_app_for_fetch, monkeypatch):
    """NVR fmp4 回 401/403 → AUTH_FAILED。

    前端看到 AUTH_FAILED 會走紅框 ⚠️ 路徑並提示重新登入。
    """
    flask_app, db_path = seeded_app_for_fetch
    import web.clips_app as ca
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr",
        lambda nvr_row, session_token: _AuthFailedClient(),
    )
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-fake-401")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-bad",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)
    ctype = resp.headers.get("Content-Type", "")
    assert "application/json" in ctype
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["error"] == "AUTH_FAILED"
    assert body.get("stage") == "nvr_auth"


def test_nvr_500_returns_internal_error_json(seeded_app_for_fetch, monkeypatch):
    """NVR fmp4 回其他 4xx/5xx → NVR_INTERNAL_ERROR（紅框 ⚠️）。

    對應 fix07.txt 原案例：NVR 回 500 + {"meta":{"code":-1,"name":"ERROR"}}
    前端看到 NVR_INTERNAL_ERROR 顯示紅框 + 「NVR 連線失敗」字串。
    """
    flask_app, db_path = seeded_app_for_fetch
    import web.clips_app as ca
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr",
        lambda nvr_row, session_token: _NvrInternalClient(),
    )
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-fake-500")

    client = flask_app.test_client()
    t0 = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1, "camera_id": "cam-bad",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(seconds=30)).isoformat(),
    }
    resp = client.post("/clips/fetch", json=payload)
    ctype = resp.headers.get("Content-Type", "")
    assert "application/json" in ctype
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["error"] == "NVR_INTERNAL_ERROR"
    # detail 應有 NVR 內部錯誤訊息
    assert "500" in body.get("detail", "") or "ERROR" in body.get("detail", "")
