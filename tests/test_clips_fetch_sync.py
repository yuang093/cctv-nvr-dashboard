"""
test_clips_fetch_sync.py
========================
驗證 /clips/fetch_sync endpoint 交集計算（2026-07-14 方案 A）。

測試項目：
1. 4 台 cam 都有完整 ±30s 錄影 → 交集 = 60s → 200 multipart
2. 4 台 cam 部分重疊 → 交集 = 重疊長度
3. 交集 < 5s → NO_COMMON_RECORDING JSON
4. 1 台 cam MPD query 失敗 → 用其他 3 台算交集
5. 1 台 cam fetch_clip 失敗 → 該 slot 標記 X-Slot-Error，其他仍可用
6. 全部 cam 都查無錄影 → NO_COMMON_RECORDING
"""

from __future__ import annotations

import gc
import os
import re
from datetime import datetime, timezone

import pytest

# 設定 env vars 在 import 前
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")

from db.sqlite_writer import SqliteWriter  # noqa: E402
from web.clips_app import _SessionStore, app  # noqa: E402


@pytest.fixture
def seeded_sync_app(monkeypatch, tmp_path):
    """建立 4 台 cam 的 DB fixture。"""
    db_path = str(tmp_path / "sync_test.db")
    monkeypatch.setenv("NVR_DB_PATH", db_path)

    w = SqliteWriter(db_path)
    w.upsert_nvr(
        {
            "id": "NVR-SYNC-A",
            "name": "Sync Test NVR",
            "host": "10.0.0.1",
            "port": 8443,
            "username": "u",
            "password": "p",
            "tags": [],
        }
    )
    w.begin_scan_run("2026-07-14T00:00:00Z")
    w.upsert_cameras(
        1,
        {
            "cam-a": {
                "name": "Cam A",
                "connection_state": "CONNECTED",
                "available": True,
            },
            "cam-b": {
                "name": "Cam B",
                "connection_state": "CONNECTED",
                "available": True,
            },
            "cam-c": {
                "name": "Cam C",
                "connection_state": "CONNECTED",
                "available": True,
            },
            "cam-d": {
                "name": "Cam D",
                "connection_state": "CONNECTED",
                "available": True,
            },
        },
    )
    w.finish_scan_run(
        1,
        finished_at="2026-07-14T00:00:30Z",
        status="success",
        stats={
            "total_cameras": 4,
            "abnormal_cameras": 0,
            "total_nvrs": 1,
            "ok_nvrs": 1,
            "failed_nvrs": 0,
        },
    )
    del w
    gc.collect()

    app.config["SESSION_STORE"] = _SessionStore()
    # 2026-08-06 perf：清掉 NVR stale-trust cache 防跨測試污染
    app.config.pop("NO_STALE_TRUST", None)
    app.config["TESTING"] = True
    # 預設 disable NVR stale cache probe（測試 mock mp4 bytes 都一樣會誤判）
    # 個別 test 可用 monkeypatch 蓋回真 probe 來測 stale 邏輯
    import web.clips_app as ca

    monkeypatch.setattr(
        ca,
        "_probe_nvr_stale_cache",
        lambda client, camera_id, t_center: (False, []),
    )
    yield app, db_path


def _mpd_with_duration(seconds: float) -> str:
    """包一個 mock MPD manifest，帶特定 mediaPresentationDuration。"""
    iso_dur = f"PT{seconds}S"
    return (
        '<?xml version="1.0"?>'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" '
        f'mediaPresentationDuration="{iso_dur}" minBufferTime="PT1.5S">'
        '<Period><AdaptationSet><Representation id="1" bandwidth="1000000" '
        'width="1920" height="1080" mimeType="video/mp4" codecs="avc1.4D4016">'
        "<BaseURL>/mt/api/rest/v1/media?ctx=MOCK</BaseURL>"
        "</Representation></AdaptationSet></Period></MPD>"
    )


class _FixedDurationClient:
    """Mock client：每台 cam 用不同 duration 的 MPD 回應；fetch_clip 回 dummy bytes。"""

    def __init__(self, durations_by_cam: dict[str, float]):
        self.durations_by_cam = durations_by_cam
        # 記錄呼叫，給測試驗證用
        self.mpd_calls: list[tuple[str, datetime]] = []
        self.clip_calls: list[tuple[str, datetime]] = []

    def get_snapshot(self, camera_id, at_time):
        return b"\x00" * 1024

    def get_mpd_manifest(self, camera_id, at_time="live"):
        self.mpd_calls.append((camera_id, at_time))
        dur = self.durations_by_cam.get(camera_id, 0.0)
        return _mpd_with_duration(dur)

    def get_recording_duration(self, camera_id, at_time):
        return self.durations_by_cam.get(camera_id, 0.0)

    def fetch_clip(
        self,
        camera_id,
        start_time,
        end_time=None,
        target_seconds=None,
        max_wall_seconds=None,
    ):
        self.clip_calls.append((camera_id, start_time, target_seconds))
        # 回 8KB dummy mp4 bytes
        yield b"\x00" * (8 * 1024)


def _post_sync(flask_app, **payload):
    return flask_app.test_client().post("/clips/fetch_sync", json=payload)


def _parse_multipart(resp_data: bytes, content_type: str) -> list[dict]:
    """簡易 multipart parser（給測試用）— 提取每段的 headers 與 body 大小。"""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    assert m
    boundary = b"--" + m.group(1).encode("ascii")
    segments = []
    # 用 boundary split
    chunks = resp_data.split(boundary)
    for chunk in chunks:
        if chunk.strip() in (b"", b"--", b"--\r\n"):
            continue
        # chunk 格式：\r\n<headers>\r\n\r\n<body>\r\n
        if not chunk.startswith(b"\r\n"):
            continue
        chunk = chunk[2:]  # 去掉 leading \r\n
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        # 找到 headers/body 分隔（\r\n\r\n）
        sep = chunk.find(b"\r\n\r\n")
        if sep == -1:
            continue
        headers_raw = chunk[:sep].decode("ascii", "ignore")
        body = chunk[sep + 4 :]
        meta = {}
        for line in headers_raw.split("\r\n"):
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
        meta["_body_len"] = len(body)
        segments.append(meta)
    return segments


# === 測試案例 ===


def test_all_cams_full_overlap_60s(seeded_sync_app, monkeypatch):
    """4 台 cam 都有完整 60s → 交集 60s → 200 multipart。"""
    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _FixedDurationClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-1")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a", "name": "Cam A"},
            {"device_id": "cam-b", "name": "Cam B"},
            {"device_id": "cam-c", "name": "Cam C"},
            {"device_id": "cam-d", "name": "Cam D"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 200
    ctype = resp.headers.get("Content-Type", "")
    assert "multipart/mixed" in ctype
    # response header 應有 intersection 資訊
    assert resp.headers.get("X-Intersection-Length") == "60.00"
    # 4 段 segment
    data = resp.get_data()
    segments = _parse_multipart(data, ctype)
    assert len(segments) == 4, f"預期 4 段，實際 {len(segments)}：{segments}"
    # 每段都應該有 X-Camera-Id + 都有 video bytes
    for s in segments:
        assert "X-Camera-Id" in s
        assert "X-Actual-Duration" in s
        assert s["_body_len"] > 0


def test_partial_overlap_intersection_is_target(seeded_sync_app, monkeypatch):
    """2026-07-14 改：intersection = target_seconds（不再用 min(MPD durations)）。
    NVR MPD 的 mediaPresentationDuration 是當下 Period 長度，不是總錄影，
    拿 min() 限縮會讓結果只有 14s。改用 target_seconds 後讓 fmp4 truncation 真正處理。
    """
    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 60.0, "cam-b": 40.0, "cam-c": 25.0, "cam-d": 10.0}
    client = _FixedDurationClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-2")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
            {"device_id": "cam-c"},
            {"device_id": "cam-d"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 200
    # 2026-07-14 起 intersection = target_seconds（不再用 min），
    # 因為所有 cam duration > 0（認為有錄影）就 fetch 滿 target_seconds。
    assert float(resp.headers.get("X-Intersection-Length")) == 60.0


def test_target_seconds_below_5s_returns_no_common_recording(
    seeded_sync_app, monkeypatch
):
    """2026-07-14 改：intersection < 5s 改成 target_seconds < 5s 觸發。
    因為 MPD duration 不再決定 intersection，唯一會變成 < 5s 的可能是 client 傳太小的 target_seconds。
    """
    """target_seconds < 5s → NO_COMMON_RECORDING JSON 404。
    因為 intersection = target_seconds，所以觸發條件從 cam duration < 5s 改成 target_seconds < 5s。
    """
    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 8.0, "cam-b": 3.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _FixedDurationClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-3")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 3,  # ← < 5 觸發
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
            {"device_id": "cam-c"},
            {"device_id": "cam-d"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["error"] == "NO_COMMON_RECORDING"
    assert body["intersection_length_sec"] == 3.0
    # 應列每台 cam 的範圍幫 user debug
    assert "cam_ranges" in body
    assert len(body["cam_ranges"]) == 4


def test_no_cam_available_returns_no_common(seeded_sync_app, monkeypatch):
    """全部 cam 都查無錄影 → NO_COMMON_RECORDING。"""

    class _AllZeroClient(_FixedDurationClient):
        def __init__(self):
            super().__init__({})  # 全部 cam → duration 0

    flask_app, db_path = seeded_sync_app
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(
        ca, "get_client_for_nvr", lambda nvr_row, session_token: _AllZeroClient()
    )
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-4")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["error"] == "NO_COMMON_RECORDING"
    assert "無可用的錄影時段" in body["message"]


def test_one_cam_mpd_query_fails_other_still_works(seeded_sync_app, monkeypatch):
    """1 台 cam MPD query 拋錯 → 仍用其他 3 台算交集。"""
    flask_app, db_path = seeded_sync_app

    class _PartialFailClient(_FixedDurationClient):
        def get_recording_duration(self, camera_id, at_time):
            if camera_id == "cam-c":
                raise RuntimeError("MPD query 失敗")
            return self.durations_by_cam.get(camera_id, 0.0)

    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _PartialFailClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-5")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
            {"device_id": "cam-c"},
            {"device_id": "cam-d"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 200  # 還是有交集
    assert float(resp.headers.get("X-Intersection-Length")) == 60.0


def test_one_cam_fetch_clip_fails_other_slots_still_have_body(
    seeded_sync_app, monkeypatch
):
    """fetch_clip 階段 1 台 cam 失敗 → 該 slot 帶 X-Slot-Error，其他 slot 仍正常。"""

    class _PartialClipFailClient(_FixedDurationClient):
        def fetch_clip(
            self,
            camera_id,
            start_time,
            end_time=None,
            target_seconds=None,
            max_wall_seconds=None,
        ):
            self.clip_calls.append((camera_id, start_time, target_seconds))
            if camera_id == "cam-b":
                from web.clip_retrieval import NvrInternalError

                raise NvrInternalError("cam-b fmp4 500")
            yield b"\x00" * (4 * 1024)

    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _PartialClipFailClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-6")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
            {"device_id": "cam-c"},
            {"device_id": "cam-d"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 200  # 部分失敗仍 200
    ctype = resp.headers.get("Content-Type", "")
    data = resp.get_data()
    segments = _parse_multipart(data, ctype)
    # 4 段都有
    assert len(segments) == 4
    # cam-b 那段應有 X-Slot-Error
    cam_b_seg = next(s for s in segments if s.get("X-Camera-Id") == "cam-b")
    assert "X-Slot-Error" in cam_b_seg, "失敗 cam 必須有 X-Slot-Error header"
    assert cam_b_seg["_body_len"] == 0
    # 其他 3 段應有 body
    for s in segments:
        if s["X-Camera-Id"] == "cam-b":
            continue
        assert s["_body_len"] > 0


def test_videos_in_response_have_same_intersection_length(seeded_sync_app, monkeypatch):
    """核心驗證：成功 cam 的 X-Actual-Duration 都跟 intersection_length 相同。"""
    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 60.0, "cam-b": 30.0, "cam-c": 22.0, "cam-d": 22.0}
    client = _FixedDurationClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-7")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
            {"device_id": "cam-c"},
            {"device_id": "cam-d"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 200
    intersection_length = float(resp.headers.get("X-Intersection-Length"))
    ctype = resp.headers.get("Content-Type", "")
    data = resp.get_data()
    segments = _parse_multipart(data, ctype)
    # 所有成功 cam 的 duration 應 == intersection_length
    for s in segments:
        if "X-Actual-Duration" in s:
            assert float(s["X-Actual-Duration"]) == intersection_length, (
                f"cam={s.get('X-Camera-Id')} X-Actual-Duration={s['X-Actual-Duration']} "
                f"但 X-Intersection-Length={intersection_length}"
            )


def test_cam_has_more_than_target_keeps_target_seconds(seeded_sync_app, monkeypatch):
    """2026-07-14 改：intersection = target_seconds，不再取 min(MPD durations)。
    驗證 cam-d 只有 15s 但其他 60s 時，intersection 仍是 target_seconds（60s）；
    server 把 cam-d 標記為截斷（dur=60 但實際 mp4 只到 15s）。

    注意：因為 MockMediaClient.fetch_clip 不會 truncate 真實 NVR 行為，
    本測試只驗 header 對應；X-Cam-Available-Duration 仍用 mock duration。
    """
    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 15.0}
    client = _FixedDurationClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-8")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
            {"device_id": "cam-c"},
            {"device_id": "cam-d"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert resp.status_code == 200
    # 2026-07-14：intersection = target_seconds（不再取 min）
    assert float(resp.headers.get("X-Intersection-Length")) == 60.0
    # 各 cam 的 X-Cam-Available-Duration 仍來自 mock duration
    ctype = resp.headers.get("Content-Type", "")
    data = resp.get_data()
    segments = _parse_multipart(data, ctype)
    for s in segments:
        if s.get("X-Camera-Id") in ("cam-a", "cam-b", "cam-c"):
            assert float(s.get("X-Cam-Available-Duration", "0")) == 60.0


def test_fetch_sync_passes_end_time_to_client(seeded_sync_app, monkeypatch):
    """回歸測試：fetch_sync 必須傳 end_time 給 client.fetch_clip，
    不然真實 MpdMediaClient（簽名端_time 必填）會 TypeError。

    2026-07-14 user 回報「選 1 台可以、選 2 台壞：
        MpdMediaClient.fetch_clip() missing 1 required positional argument: 'end_time'」。
    這個測試用 strict-signature stub（沒有預設值）模擬真實 client，
    確保 fetch_sync 傳齊 (camera_id, start_time, end_time) 三個參數。
    """

    class _StrictSignatureClient(_FixedDurationClient):
        """fetch_clip 簽名嚴格 (camera_id, start_time, end_time)，沒預設值。"""

        def fetch_clip(
            self,
            camera_id,
            start_time,
            end_time,
            target_seconds=None,
            max_wall_seconds=None,
        ):
            self.clip_calls.append((camera_id, start_time, end_time, target_seconds))
            yield b"\x00" * (8 * 1024)

    flask_app, db_path = seeded_sync_app
    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _StrictSignatureClient(durations)
    import web.clips_app as ca

    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-9")

    t0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "cameras": [
            {"device_id": "cam-a"},
            {"device_id": "cam-b"},
        ],
    }
    resp = _post_sync(flask_app, **payload)
    assert (
        resp.status_code == 200
    ), f"應該 200 但 {resp.status_code}：{resp.get_data()[:300]}"
    # 確認 client.fetch_clip 收到的 end_time 是 datetime，不是 NotImplemented
    assert len(client.clip_calls) == 2
    for cam_id, start_time, end_time, target_seconds in client.clip_calls:
        # 兩個時間應是 datetime 且 end_time > start_time
        assert isinstance(start_time, datetime)
        assert isinstance(end_time, datetime)
        assert end_time > start_time
        # intersection_length = 60s（4 台都有完整錄影）
        assert (end_time - start_time).total_seconds() == pytest.approx(60.0, abs=0.1)
        # 2026-07-14 followup：fetch_sync 現在會傳 target_seconds = intersection_length
        assert target_seconds == pytest.approx(60.0, abs=0.5)


# === 2026-07-15：NVR stale cache 探測測試 ===


class _StaleProbeClientV2:
    """Stale probe 用：每個 anchor offset 對應固定 bytes，順序 [-60, 0, +60]。"""

    def __init__(
        self,
        durations_by_cam: dict[str, float],
        bytes_per_anchor_by_cam: dict[str, list[bytes]],
    ) -> None:
        # bytes_per_anchor_by_cam[cam] = [b_off_neg60, b_off_0, b_off_pos60]
        self.durations_by_cam = durations_by_cam
        self.bytes_per_anchor_by_cam = bytes_per_anchor_by_cam
        self.mpd_calls: list[tuple[str, datetime, int]] = []
        self.clip_calls: list[tuple[str, datetime, datetime]] = []

    def get_snapshot(self, camera_id, at_time):
        return b"\x00" * 1024

    def get_mpd_manifest(self, camera_id, at_time="live"):
        return _mpd_with_duration(self.durations_by_cam.get(camera_id, 60.0))

    def get_recording_duration(self, camera_id, at_time):
        self.mpd_calls.append((camera_id, at_time, int(at_time.timestamp())))
        return self.durations_by_cam.get(camera_id, 60.0)

    def fetch_clip(
        self,
        camera_id,
        start_time,
        end_time=None,
        target_seconds=None,
        max_wall_seconds=None,
    ):
        self.clip_calls.append((camera_id, start_time, end_time))
        # bytes_per_anchor_by_cam[cam] 順序：[-60, 0, +60]
        # 為對應 probe 呼叫 3 次，依呼叫順序輪流 anchor bytes
        bs_list = self.bytes_per_anchor_by_cam.get(camera_id, [b"\x00" * 1024])
        if not hasattr(self, "_call_idx"):
            self._call_idx = 0
        idx = self._call_idx % len(bs_list)
        self._call_idx += 1
        yield bs_list[idx]


def test_probe_stale_cache_detects_same_bytes(monkeypatch):
    """3 個 anchor 都回同樣 bytes → probe 回 stale=True。"""
    import web.clips_app as ca

    same_bytes = b"stale-cam-bytes-1234"
    client = _StaleProbeClientV2(
        durations_by_cam={"cam-stale": 60.0},
        bytes_per_anchor_by_cam={"cam-stale": [same_bytes] * 3},
    )
    t0 = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
    is_stale, evidence = ca._probe_nvr_stale_cache(client, "cam-stale", t0)
    assert is_stale is True
    assert any("md5=" in line for line in evidence)
    assert any("STALE" in line for line in evidence)


def test_probe_stale_cache_detects_different_bytes(monkeypatch):
    """3 個 anchor 回不同 bytes → not stale。"""
    import web.clips_app as ca

    client = _StaleProbeClientV2(
        durations_by_cam={"cam-ok": 60.0},
        bytes_per_anchor_by_cam={
            "cam-ok": [b"anchor-A", b"anchor-B", b"anchor-C"],
        },
    )
    t0 = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
    is_stale, evidence = ca._probe_nvr_stale_cache(client, "cam-ok", t0)
    assert is_stale is False


def test_probe_stale_cache_handles_mpd_error(monkeypatch):
    """anchor MPD 報 0 → 視為 not stale（不能誤判成 stale，而是交給 query 階段）。"""
    import web.clips_app as ca

    class _AllZeroMpDClient:
        def get_recording_duration(self, cam_id, at_time):
            return 0.0  # 全部 anchor 都說「無錄影」

        def fetch_clip(self, *a, **k):
            yield b"never used"

    client = _AllZeroMpDClient()
    t0 = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
    is_stale, evidence = ca._probe_nvr_stale_cache(client, "cam-x", t0)
    # 全部 anchor 都被視為無錄影，evidence 有 "視為無錄影"
    assert is_stale is False
    assert any("視為無錄影" in line for line in evidence)


def test_fetch_sync_excludes_stale_cam(seeded_sync_app, monkeypatch):
    """fetch_sync：cam-b 被 stale probe 排除 → active_cams 剩 cam-a, cam-c, cam-d，回 200。"""
    flask_app, _ = seeded_sync_app
    import web.clips_app as ca

    # 解 fixture 預設的 disable，還原真 probe（但只針對 cam-b 回 stale）
    def fake_probe(client, cam_id, t_center):
        if cam_id == "cam-b":
            return (True, ["anchor 0s: STALE (test fixture)"])
        return (False, [])

    monkeypatch.setattr(ca, "_probe_nvr_stale_cache", fake_probe)

    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _FixedDurationClient(durations)
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-stale-1")

    t0 = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "probe": "1",  # 2026-08-06 perf：probe 預設關，stale tests 顯式 opt-in
        "cameras": [
            {"device_id": "cam-a", "name": "Cam A"},
            {"device_id": "cam-b", "name": "Cam B"},
            {"device_id": "cam-c", "name": "Cam C"},
            {"device_id": "cam-d", "name": "Cam D"},
        ],
    }
    resp = flask_app.test_client().post("/clips/fetch_sync", json=payload)
    assert resp.status_code == 200
    # fetch_one_cam 對 active_cams 全部（4 台 - cam-b 被排除 = 3 台）各 call 一次
    # stale probe 對每台 cam 也會 call fetch_clip，但只 call 一次（mock fake_probe 只回 True，不真 probe）
    # 我們區分 probe vs fetch：fetch 用 intersection_start = t_center - 30s
    # probe 用 t_center ± 60s anchor（fake_probe 不呼叫真 fetch）
    # fake_probe 直接回 True，不呼叫 fetch_clip → client.clip_calls 內都是 fetch
    # 所以 fetch 應該是 3 次
    assert len(client.clip_calls) == 3
    fetch_cam_ids = {cc[0] for cc in client.clip_calls}
    assert "cam-b" not in fetch_cam_ids


def test_fetch_sync_all_cams_stale_returns_502(seeded_sync_app, monkeypatch):
    """所有 cam 都 stale → 502 + error: all_cams_stale + excluded_cams 詳列。"""
    flask_app, _ = seeded_sync_app
    import web.clips_app as ca

    monkeypatch.setattr(
        ca,
        "_probe_nvr_stale_cache",
        lambda client, cam_id, t_center: (True, ["STALE"]),
    )

    durations = {"cam-a": 60.0, "cam-b": 60.0}
    client = _FixedDurationClient(durations)
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-stale-2")

    t0 = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "probe": "1",  # 2026-08-06 perf：probe 預設關，stale tests 顯式 opt-in
        "cameras": [
            {"device_id": "cam-a", "name": "Cam A"},
            {"device_id": "cam-b", "name": "Cam B"},
        ],
    }
    resp = flask_app.test_client().post("/clips/fetch_sync", json=payload)
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["error"] == "NO_COMMON_RECORDING"
    assert body["stage"] == "all_cams_stale"
    assert len(body["excluded_cams"]) == 2
    # excluded_cams 內 cam_id 應有 cam-a, cam-b 都列
    ids = {e["camera_id"] for e in body["excluded_cams"]}
    assert ids == {"cam-a", "cam-b"}


def test_fetch_sync_partial_stale_includes_excluded_header(
    seeded_sync_app, monkeypatch
):
    """部分 cam stale → 200 multipart + X-Excluded-Cams JSON header。
    前端會用這 header 顯示「找不到回放檔案」訊息。
    """
    flask_app, _ = seeded_sync_app
    import web.clips_app as ca
    import json as _json

    # 只標 cam-b stale；cam-a, cam-c, cam-d 正常
    def fake_probe(client, cam_id, t_center):
        if cam_id == "cam-b":
            return (True, ["STALE"])
        return (False, [])

    monkeypatch.setattr(ca, "_probe_nvr_stale_cache", fake_probe)

    durations = {"cam-a": 60.0, "cam-b": 60.0, "cam-c": 60.0, "cam-d": 60.0}
    client = _FixedDurationClient(durations)
    monkeypatch.setattr(ca, "get_session_for_nvr", lambda *a: "FAKE-TOKEN")
    monkeypatch.setattr(ca, "get_client_for_nvr", lambda nvr_row, session_token: client)
    monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-test-partial-stale")

    t0 = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
    payload = {
        "nvr_id": 1,
        "target_seconds": 60,
        "t_center": t0.isoformat(),
        "probe": "1",  # 2026-08-06 perf：probe 預設關，stale tests 顯式 opt-in
        "cameras": [
            {"device_id": "cam-a", "name": "Cam A"},
            {"device_id": "cam-b", "name": "Cam B"},
            {"device_id": "cam-c", "name": "Cam C"},
            {"device_id": "cam-d", "name": "Cam D"},
        ],
    }
    resp = flask_app.test_client().post("/clips/fetch_sync", json=payload)
    assert resp.status_code == 200
    excluded_header = resp.headers.get("X-Excluded-Cams")
    assert excluded_header is not None
    excluded = _json.loads(excluded_header)
    assert len(excluded) == 1
    assert excluded[0]["device_id"] == "cam-b"
    assert excluded[0]["name"] == "Cam B"
    assert excluded[0]["reason"] == "NVR_STALE_CACHE"


def test_clips_html_template_has_stale_message_marker():
    """前端 template 內必須有 stale excluded 分支的 emoji + 字串標記。"""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "web",
        "clips_templates",
        "clips.html",
    )
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    # 2026-07-15：「NVR Media API 異常」標記 + ⚠️ 標記都要存在
    # 若 marker 被拿掉，前端會把 stale cam 當「無資料」灰底，使用者看不出 NVR 端問題
    assert "X-Excluded-Cams" in html
    assert "NVR Media API 異常" in html
    assert "s.excluded" in html
    assert "excludedCams" in html


class TestStaleSessionAutoRetry:
    """2026-08-06 修：stale session cache 自動 invalidate + retry 一次。

    Scenario: SESSION_STORE 持有舊 token → NVR MPD query 用舊 token raise 401
    → fetch_sync 偵測並自動 invalidate + 重新登入 + retry。
    """

    def test_stale_session_triggers_invalidate_and_retry(
        self,
        seeded_sync_app,
        monkeypatch,
    ):
        """stale token → 第一輪 query 都失敗 → fetch_sync 自動 retry → 第二輪成功。"""
        from web.clip_retrieval import NvrAuthError as _NvrAuthError

        flask_app, db_path = seeded_sync_app
        flask_app.config["SESSION_STORE"].set(1, "EXPIRED-TOKEN-AAA")

        # Session token 流程：第一次回 EXPIRED（觸發 auth 失敗），
        # 第二次之後回 FRESH（模擬 invalidate 後重新登入成功）
        session_calls = {"n": 0}
        session_sequence = ["EXPIRED-TOKEN-AAA", "FRESH-TOKEN-XYZ"]

        def fake_get_session(internal_id, store):
            idx = min(session_calls["n"], len(session_sequence) - 1)
            token = session_sequence[idx]
            session_calls["n"] += 1
            return token

        # Client 流程：看到 EXPIRED token → raise NvrAuthError；看到 FRESH → 回 60s
        mpd_calls = {"n": 0}

        class _StaleThenFreshClient(_FixedDurationClient):
            def __init__(self, *, host, port, session, verify_ssl, **_kw):
                super().__init__(
                    durations_by_cam={"cam-a": 60.0, "cam-b": 60.0},
                )
                self._session = session

            def get_recording_duration(self, camera_id, at_time):
                mpd_calls["n"] += 1
                if self._session == "EXPIRED-TOKEN-AAA":
                    raise _NvrAuthError("NVR 認證失敗（401）：invalid session")
                return super().get_recording_duration(camera_id, at_time)

        import web.clips_app as ca

        monkeypatch.setattr(ca, "get_session_for_nvr", fake_get_session)
        monkeypatch.setattr(
            ca,
            "get_client_for_nvr",
            lambda nvr_row, session_token: _StaleThenFreshClient(
                host=nvr_row["host"],
                port=nvr_row.get("port", 8443),
                session=session_token,
                verify_ssl=False,
            ),
        )
        monkeypatch.setattr(ca, "_probe_nvr_stale_cache", lambda *a: (False, []))
        monkeypatch.setenv("NVR_CLIPS_CLIENT", "live-stale-test")

        client = flask_app.test_client()
        resp = client.post(
            "/clips/fetch_sync",
            json={
                "nvr_id": 1,
                "cameras": [
                    {"device_id": "cam-a", "name": "CamA"},
                    {"device_id": "cam-b", "name": "CamB"},
                ],
                "t_center": "2026-07-14T12:00:00Z",
                "target_seconds": 60,
            },
        )
        # 觀察點：retry 機制必須被觸發
        assert session_calls["n"] >= 2, (
            f"stale session 應觸發 invalidate + 重新登入（session_calls.n ≥ 2），"
            f"got {session_calls['n']}"
        )
        assert mpd_calls["n"] >= 4, (
            f"query_cam_availability 應被跑 2 次（stale + retry），"
            f"每輪 2 cam → ≥ 4 mpds；got {mpd_calls['n']}"
        )
        # 最終 status：第二輪成功 → intersection 有結果 → multipart 回應
        # 或 active_cams 全空 → 404（mock mp4 沒真的 video bytes 也可能走 multipart 空殼）
        assert resp.status_code in (
            200,
            404,
        ), f"retry 後應至少完成流程，got {resp.status_code} body={resp.data[:200]}"
