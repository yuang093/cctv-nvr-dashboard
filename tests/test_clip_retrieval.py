"""
tests/test_clip_retrieval.py
============================
Phase 2.7 — `web/clip_retrieval.py` 單元測試。

涵蓋：
* MediaApiClient Protocol 結構檢查
* MockMediaClient 三 method 行為
* MpdMediaClient 佔位實作（NotImplementedError）
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from web.clip_retrieval import (
    MediaApiClient,
    MockMediaClient,
    MpdMediaClient,
    _parse_iso8601_duration_to_seconds,
)


# === MediaApiClient Protocol ===


def test_mock_satisfies_protocol():
    """MockMediaClient 必須被視為 MediaApiClient 實例。"""
    m = MockMediaClient()
    assert isinstance(m, MediaApiClient)


def test_mpdclient_construction():
    """MpdMediaClient 基本建構（佔位實作不需真 NVR）。"""
    c = MpdMediaClient(host="1.2.3.4", port=8443, session="FAKE-TOKEN")
    assert c._base_url == "https://1.2.3.4:8443/mt/api/rest/v1/media"
    assert c._session == "FAKE-TOKEN"
    assert c._verify_ssl is False


def test_mpdclient_supported_formats_constant():
    """MpdMediaClient 應列出 spec 上的 6 種 format。"""
    assert set(MpdMediaClient.SUPPORTED_FORMATS) == {
        "mpd",
        "fmp4",
        "jpeg",
        "json",
        "webm",
        "spkc",
    }


# === MockMediaClient 三 method ===


def test_mock_get_snapshot_returns_bytes_of_expected_size():
    m = MockMediaClient(snapshot_bytes=512)
    at = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
    out = m.get_snapshot("cam-001", at)
    assert isinstance(out, bytes)
    assert len(out) == 512
    # 呼叫記錄
    assert m.snapshot_calls == [("cam-001", at)]


def test_mock_get_mpd_manifest_returns_xml_string():
    m = MockMediaClient()
    out = m.get_mpd_manifest("cam-001")
    assert isinstance(out, str)
    assert "<MPD" in out
    assert "<BaseURL>" in out
    assert "cameraId" in out or "MOCK" in out
    assert m.manifest_calls == ["cam-001"]


def test_mock_fetch_clip_yields_chunks():
    m = MockMediaClient(clip_chunks=3, clip_chunk_size=128)
    start = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 6, 12, 0, 30, tzinfo=timezone.utc)
    chunks = list(m.fetch_clip("cam-001", start, end))
    assert len(chunks) == 3
    assert all(len(c) == 128 for c in chunks)
    assert m.clip_calls == [("cam-001", start, end)]


def test_mock_records_multiple_calls():
    m = MockMediaClient()
    at = datetime(2026, 7, 6, tzinfo=timezone.utc)
    m.get_snapshot("a", at)
    m.get_snapshot("b", at)
    m.get_mpd_manifest("a")
    m.get_mpd_manifest("b")
    m.get_mpd_manifest("c")
    assert len(m.snapshot_calls) == 2
    assert m.manifest_calls == ["a", "b", "c"]


# === MockMediaClient get_recording_duration（user 032.PNG 修法）===
# 2026-08-04 user 032.PNG：點 coverage 綠帶跳 /clips → 2 台 cam 走 /clips/fetch_sync
# → clips_app.py:843 對每台 cam 呼叫 client.get_recording_duration() → MockMediaClient
# AttributeError → 兩台 cam 都 MPD query FAILED → NO_COMMON_RECORDING → UI 顯示
# 「無共同錄影時段」「NVR 連線失敗」（實際是 mock client 缺方法）。
# 修法：MockMediaClient 必須實作 get_recording_duration，Protocol 也要納入。


def test_mock_has_get_recording_duration_method():
    """MockMediaClient 必須實作 get_recording_duration（防 mock drift）。

    歷史教訓：MpdMediaClient 加了新方法，Protocol 沒納入 → mock 沒實作 → 生產炸、
    測試綠（測試用自訂 mock client）。
    """
    m = MockMediaClient()
    assert hasattr(
        m, "get_recording_duration"
    ), "MockMediaClient 缺 get_recording_duration（user 032.PNG regression）"
    assert callable(m.get_recording_duration), "get_recording_duration 必須可呼叫"


def test_mock_get_recording_duration_returns_float():
    """Mock get_recording_duration 回 float（給 clips_app.py 直接加 timedelta）。"""
    m = MockMediaClient()
    at = datetime(2026, 8, 4, 9, 24, 0, tzinfo=timezone.utc)
    dur = m.get_recording_duration("cam-001", at)
    assert isinstance(
        dur, float
    ), "duration 應為 float（與 MpdMediaClient.get_recording_duration 簽名一致）"
    # mock 應回 > 0（讓 fetch_sync 進入 active_cams 不會誤判「無錄影」）
    assert (
        dur > 0.0
    ), "mock 預設 duration > 0（fetch_sync 需要至少 1 台 cam 進入 active_cams）"


def test_protocol_declares_get_recording_duration():
    """MediaApiClient Protocol 必須包含 get_recording_duration（防 mock drift）。

    歷史教訓：方法只在 MpdMediaClient 實作，Protocol 沒宣告 → mock 不被提醒實作。
    """
    protocol_attrs = set(dir(MediaApiClient))
    assert (
        "get_recording_duration" in protocol_attrs
    ), "MediaApiClient Protocol 應宣告 get_recording_duration（防 mock drift）"


# === MpdMediaClient 實作（2026-07-07 已實測可連 NVR 192.168.133.141） ===


def test_mpdclient_format_t_passes_through_live():
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")
    assert c._format_t("live") == "live"


def test_mpdclient_format_t_adds_milliseconds_and_z():
    """ISO 8601 沒毫秒 / 沒 Z 結尾會被 NVR 拒（回 400），要自動補。"""
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")
    # datetime → "YYYY-MM-DDTHH:MM:SS.fffZ"
    at = datetime(2026, 7, 7, 8, 0, 0, tzinfo=timezone.utc)
    out = c._format_t(at)
    assert out == "2026-07-07T08:00:00.000Z"
    # 帶 Z 沒毫秒的字串 → 補 .000Z
    assert c._format_t("2026-07-07T08:00:00Z") == "2026-07-07T08:00:00.000Z"


def test_mpdclient_format_t_naive_datetime_treated_as_utc():
    """沒 tzinfo 的 datetime 應視為 UTC（避免 +8h 誤判）。"""
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")
    naive = datetime(2026, 7, 7, 8, 0, 0)  # no tzinfo
    assert c._format_t(naive) == "2026-07-07T08:00:00.000Z"


def test_mpdclient_get_snapshot_calls_jpeg_endpoint(monkeypatch):
    """get_snapshot 必須打 format=jpeg，回 bytes。"""
    c = MpdMediaClient(host="1.2.3.4", session="FAKE-TOKEN")

    class FakeResp:
        status_code = 200
        content = b"\xff\xd8\xff\xe0JFIF-FAKE"

        def close(self):
            pass

    captured = {}

    def fake_get(self, url, params, timeout, verify, stream):
        captured["url"] = url
        captured["params"] = params
        return FakeResp()

    monkeypatch.setattr(c._http, "get", fake_get.__get__(c._http))

    at = datetime(2026, 7, 7, 8, 0, 0, tzinfo=timezone.utc)
    out = c.get_snapshot("cam-X", at)
    assert out == b"\xff\xd8\xff\xe0JFIF-FAKE"
    assert captured["params"]["format"] == "jpeg"
    assert captured["params"]["cameraId"] == "cam-X"
    assert captured["params"]["session"] == "FAKE-TOKEN"
    assert captured["params"]["t"] == "2026-07-07T08:00:00.000Z"


def test_mpdclient_get_snapshot_raises_on_non_200(monkeypatch):
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")

    class FakeResp:
        status_code = 401
        text = "session expired"

        def close(self):
            pass

    monkeypatch.setattr(c._http, "get", lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError, match="401"):
        c.get_snapshot("cam-X", datetime(2026, 7, 7, tzinfo=timezone.utc))


def test_mpdclient_get_mpd_calls_mpd_endpoint(monkeypatch):
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")

    class FakeResp:
        status_code = 200
        content = b'<?xml version="1.0"?><MPD>fake</MPD>'

        def close(self):
            pass

    captured = {}

    def fake_get(self, url, params, timeout, verify, stream):
        captured["params"] = params
        return FakeResp()

    monkeypatch.setattr(c._http, "get", fake_get.__get__(c._http))
    out = c.get_mpd_manifest("cam-X", datetime(2026, 7, 7, tzinfo=timezone.utc))
    assert "<MPD>" in out
    assert captured["params"]["format"] == "mpd"


def test_mpdclient_fetch_clip_calls_fmp4_and_streams(monkeypatch):
    """fetch_clip 必須用 format=fmp4 並 iter_content yield chunks。"""
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")
    chunks_emitted = [b"AAAA", b"BBBB", b"CCCC"]

    class FakeResp:
        status_code = 200

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            for ck in chunks_emitted:
                yield ck

        def close(self):
            pass

    captured = {}

    def fake_get(self, url, params, timeout, verify, stream):
        captured["params"] = params
        return FakeResp()

    monkeypatch.setattr(c._http, "get", fake_get.__get__(c._http))
    start = datetime(2026, 7, 7, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 7, 8, 0, 30, tzinfo=timezone.utc)
    out = list(c.fetch_clip("cam-X", start, end))
    assert out == chunks_emitted
    assert captured["params"]["format"] == "fmp4"


def test_mpdclient_fetch_clip_raises_on_non_200(monkeypatch):
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")

    class FakeResp:
        status_code = 400
        text = "bad t"

        def close(self):
            pass

    monkeypatch.setattr(c._http, "get", lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError, match="400"):
        list(
            c.fetch_clip(
                "cam-X",
                datetime(2026, 7, 7, tzinfo=timezone.utc),
                datetime(2026, 7, 7, 0, 0, 30, tzinfo=timezone.utc),
            )
        )


# === ISO 8601 duration parser（給擴搜用）===


@pytest.mark.parametrize(
    "dur,expected",
    [
        ("PT9.390S", 9.39),
        ("PT1M30.5S", 90.5),
        ("PT2H", 7200.0),
        ("PT1H30M", 5400.0),
        ("PT1H30M45S", 5445.0),
        ("PT0.5S", 0.5),
        ("PT0S", 0.0),
    ],
)
def test_parse_iso8601_duration(dur, expected):
    """NVR 會吐的 duration 格式（PT[H][M]S）— 解析要對。"""
    assert _parse_iso8601_duration_to_seconds(dur) == pytest.approx(expected, abs=0.01)


def test_parse_iso8601_duration_unparseable_returns_zero():
    """無法 parse（如 NVR 改格式或壞資料）→ 回 0.0 而不是炸。"""
    assert _parse_iso8601_duration_to_seconds("not a duration") == 0.0
    assert _parse_iso8601_duration_to_seconds("P1Y") == 0.0  # 沒處理年


def test_mpdclient_get_recording_duration_parses_mpd(monkeypatch):
    """get_recording_duration 從 MPD 解析 mediaPresentationDuration。"""
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")
    mpd_xml = (
        '<?xml version="1.0"?>'
        '<MPD type="static" mediaPresentationDuration="PT12.345S">'
        "<Period>...</Period></MPD>"
    )

    class FakeResp:
        status_code = 200
        content = mpd_xml.encode("utf-8")
        text = mpd_xml

        def close(self):
            pass

    monkeypatch.setattr(c._http, "get", lambda *a, **k: FakeResp())
    dur = c.get_recording_duration("cam-X", datetime(2026, 7, 7, tzinfo=timezone.utc))
    assert dur == pytest.approx(12.345, abs=0.01)


def test_mpdclient_get_recording_duration_no_duration_returns_zero(monkeypatch):
    """MPD 沒 mediaPresentationDuration（理論上不會）→ 回 0.0 表示無錄影。"""
    c = MpdMediaClient(host="1.2.3.4", session="FAKE")
    mpd_xml = '<?xml version="1.0"?><MPD><Period/></MPD>'

    class FakeResp:
        status_code = 200
        content = mpd_xml.encode("utf-8")
        text = mpd_xml

        def close(self):
            pass

    monkeypatch.setattr(c._http, "get", lambda *a, **k: FakeResp())
    assert (
        c.get_recording_duration("cam-X", datetime(2026, 7, 7, tzinfo=timezone.utc))
        == 0.0
    )
