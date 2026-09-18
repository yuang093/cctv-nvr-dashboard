"""
tests/test_clips_app.py
========================
Phase 2.7 — `web/clips_app.py` Flask 路由 + 並行 + session cache 測試。

策略：灌 SqliteWriter → 用 Flask test_client 跑各路由 → 檢查 status + payload。
NVR_CLIPS_CLIENT=mock 環境變數讓 client factory 回 MockMediaClient（不打真 NVR）。
"""
from __future__ import annotations

import gc
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# 設定 env vars **在 import 前**，否則 clips_app 會讀到舊值
os.environ.setdefault("NVR_CLIPS_CLIENT", "mock")


from db.sqlite_writer import SqliteWriter  # noqa: E402

# import clips_app — 模組載入時會 load .env（無害）
from web.clips_app import (  # noqa: E402
    _SessionStore, _compress_to_thumbnail, app, fetch_snapshots_parallel,
)


@pytest.fixture
def seeded_clips_app(monkeypatch, tmp_path):
    """建立灌好測試資料的檔案 DB + Flask app。"""
    db_path = str(tmp_path / "clips_test.db")
    # 確保 clips_app 用這個 DB
    monkeypatch.setenv("NVR_DB_PATH", db_path)

    w = SqliteWriter(db_path)
    w.upsert_nvr({
        "id": "NVR-CLIP-A", "name": "Clip 測試 NVR", "host": "10.0.0.1",
        "port": 8443, "username": "u", "password": "p", "tags": [],
    })
    w.begin_scan_run("2026-07-06T00:00:00Z")
    w.upsert_cameras(1, {
        "cam-001": {"name": "大門", "connection_state": "CONNECTED", "available": True},
        "cam-002": {"name": "後門", "connection_state": "CONNECTED", "available": True},
        "cam-003": {"name": "停車場", "connection_state": "LONG_FAILED", "available": False},
    })
    w.finish_scan_run(1, finished_at="2026-07-06T00:00:30Z", status="success",
                      stats={"total_cameras": 3, "abnormal_cameras": 0,
                             "total_nvrs": 1, "ok_nvrs": 1, "failed_nvrs": 0})
    del w
    gc.collect()

    # 清掉 session cache（避免跨測試污染）
    app.config["SESSION_STORE"] = _SessionStore()
    app.config["TESTING"] = True
    yield app, db_path

    # cleanup
    try:
        Path(db_path).unlink()
    except OSError:
        pass


# === 路由：GET /clips ===

def test_clips_page_renders(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips")
        assert r.status_code == 200
        body = r.data.decode("utf-8")
        assert "錄影回放調閱" in body
        assert "id=\"nvrSel\"" in body
        assert "id=\"tIn\"" in body
        assert "id=\"syncPlayBtn\"" in body
        assert "id=\"syncPlaySection\"" in body


def test_root_redirects_or_renders(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "錄影回放調閱" in r.data.decode("utf-8")


# === 路由：GET /clips/nvrs ===

def test_clips_nvrs_returns_seeded_nvr(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/nvrs")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 1
        assert data[0]["nvr_id"] == "NVR-CLIP-A"
        assert data[0]["name"] == "Clip 測試 NVR"
        assert data[0]["camera_count"] == 3


# === 路由：GET /clips/cameras ===

def test_clips_cameras_returns_camera_list(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/cameras?nvr_id=1")
        assert r.status_code == 200
        data = r.get_json()
        cam_ids = {c["device_id"] for c in data}
        assert cam_ids == {"cam-001", "cam-002", "cam-003"}


def test_clips_cameras_missing_nvr_id(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/cameras?nvr_id=9999")
        assert r.status_code == 200
        assert r.get_json() == []


def test_clips_cameras_bad_nvr_id(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/cameras?nvr_id=not-an-int")
        assert r.status_code == 400


# === 路由：GET /clips/snapshots（並行抓取）===

def test_clips_snapshots_returns_3_thumbnails(seeded_clips_app):
    """MockMediaClient 會回固定 bytes，3 台相機並行抓完應回 3 個 thumbnail。"""
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=1&t=2026-07-06T12:00:00Z")
        assert r.status_code == 200
        data = r.get_json()
        assert data["camera_count"] == 3
        assert len(data["snapshots"]) == 3
        for s in data["snapshots"]:
            assert s["ok"] is True
            assert isinstance(s["jpeg_b64"], str) and len(s["jpeg_b64"]) > 0
            assert s["raw_size"] == 1024  # MockMediaClient 預設 1024 bytes
            assert s["thumb_size"] > 0
            # 回歸測試：每個 snapshot 必須附 camera_name（給 UI 顯示用）
            # 2026-07-07 user 回報：UI 顯示 device_id 太長又看不懂，要求顯示相機名稱
            assert "camera_name" in s, "snapshot 沒帶 camera_name（UI 會被迫顯示 device_id）"
            assert isinstance(s["camera_name"], str) and len(s["camera_name"]) > 0


def test_clips_snapshots_camera_name_fallback_to_device_id(seeded_clips_app):
    """若 DB 內 cameras 表沒有對應 device_id（理論上不會發生），camera_name
    應 fallback 到 device_id，UI 至少顯示 device_id 而不是空白。"""
    app, _ = seeded_clips_app
    # 跑一次真的拿 snapshots，確認所有 snap 都有 camera_name（不是空字串）
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=1&t=2026-07-06T12:00:00Z")
        assert r.status_code == 200
        data = r.get_json()
        for s in data["snapshots"]:
            assert s.get("camera_name"), (
                f"snapshot camera_name 是空字串：device_id={s.get('camera_id')}"
            )


def test_clips_snapshots_missing_params(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots")  # 缺 nvr_id + t
        assert r.status_code == 400


def test_clips_snapshots_bad_time(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=1&t=not-a-time")
        assert r.status_code == 400


def test_clips_snapshots_unknown_nvr(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=999&t=2026-07-06T12:00:00Z")
        assert r.status_code == 404


# === 路由：POST /clips/fetch ===

def test_clips_fetch_streams_mp4_bytes(seeded_clips_app):
    """MockMediaClient.fetch_clip yield 4 chunks × 4KB = 16KB。"""
    app, _ = seeded_clips_app
    payload = {
        "nvr_id": 1,
        "camera_id": "cam-001",
        "start": "2026-07-06T11:59:45+00:00",
        "end": "2026-07-06T12:00:15+00:00",
    }
    with app.test_client() as c:
        r = c.post("/clips/fetch", json=payload)
        assert r.status_code == 200
        assert r.mimetype == "video/mp4"
        assert r.headers.get("X-Camera-Id") == "cam-001"
        # MockMediaClient 預設 4 chunks × 4096 = 16384
        assert len(r.data) == 16384


def test_clips_fetch_missing_params(seeded_clips_app):
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.post("/clips/fetch", json={"nvr_id": 1})  # 缺 camera_id
        assert r.status_code == 400


def test_clips_fetch_unknown_nvr(seeded_clips_app):
    app, _ = seeded_clips_app
    payload = {
        "nvr_id": 999,
        "camera_id": "cam-001",
        "start": "2026-07-06T11:59:45+00:00",
        "end": "2026-07-06T12:00:15+00:00",
    }
    with app.test_client() as c:
        r = c.post("/clips/fetch", json=payload)
        assert r.status_code == 404


# === SessionStore TTL 行為 ===

def test_session_store_set_get_ttl():
    s = _SessionStore(ttl_seconds=1)
    s.set(1, "TOK-A")
    assert s.get(1) == "TOK-A"
    time.sleep(1.2)
    assert s.get(1) is None  # expired


def test_session_store_clear():
    s = _SessionStore()
    s.set(1, "TOK-A")
    s.set(2, "TOK-B")
    s.clear(1)
    assert s.get(1) is None
    assert s.get(2) == "TOK-B"
    s.clear()  # 清全部
    assert s.get(2) is None


# === 並行效能 ===

def test_parallel_fetch_8_cameras_under_3_seconds():
    """8 台相機並行抓 mock 應 < 3 秒。"""
    from web.clip_retrieval import MockMediaClient
    client = MockMediaClient(snapshot_bytes=10_000)
    cam_ids = [f"cam-{i:03d}" for i in range(8)]
    at = datetime(2026, 7, 6, tzinfo=timezone.utc)
    t0 = time.time()
    snaps = fetch_snapshots_parallel(client, cam_ids, at, max_workers=8)
    elapsed = time.time() - t0
    assert len(snaps) == 8
    assert all(s["ok"] for s in snaps)
    assert elapsed < 3.0, f"parallel fetch too slow: {elapsed:.2f}s"


def test_parallel_fetch_preserves_order():
    """並行抓取後應保持原始 camera_ids 順序。"""
    from web.clip_retrieval import MockMediaClient
    client = MockMediaClient()
    cam_ids = [f"cam-{i:03d}" for i in range(5)]
    at = datetime(2026, 7, 6, tzinfo=timezone.utc)
    snaps = fetch_snapshots_parallel(client, cam_ids, at, max_workers=4)
    returned_ids = [s["camera_id"] for s in snaps]
    assert returned_ids == cam_ids


# === Pillow 縮圖 ===

def test_compress_to_thumbnail_shrinks_real_jpeg():
    """真實 JPEG 應該被縮到 60x80 thumbnail。"""
    import io
    from PIL import Image
    im = Image.new("RGB", (640, 480), "red")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    raw = buf.getvalue()
    assert len(raw) > 1000
    thumb = _compress_to_thumbnail(raw, size=(60, 80))
    assert len(thumb) < len(raw), "thumbnail should be smaller"
    # 確認 thumb 仍是有效 JPEG
    im2 = Image.open(io.BytesIO(thumb))
    assert im2.size[0] <= 80
    assert im2.size[1] <= 60


def test_compress_to_thumbnail_handles_invalid_bytes():
    """無效 bytes 應該 fallback 回原 bytes（不 crash）。"""
    bad = b"\x00" * 100
    out = _compress_to_thumbnail(bad)
    assert out == bad  # fallback


# === Mock 模式不需要真 login ===

def test_clips_snapshots_skips_login_in_mock_mode(seeded_clips_app, monkeypatch):
    """NVR_CLIPS_CLIENT=mock 時，route 不應觸發 _login_nvr。"""
    from web import clips_app

    called = {"login": False}

    def fake_login(*a, **kw):
        called["login"] = True
        return "FAKE"

    monkeypatch.setattr(clips_app, "_login_nvr", fake_login)
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=1&t=2026-07-06T12:00:00Z")
        assert r.status_code == 200
        assert called["login"] is False, "mock mode should skip _login_nvr"


# === 真實模式 _login_nvr 介面正確性（回歸測試）===

def test_login_nvr_uses_avigilon_scanner_correct_signature(monkeypatch):
    """回歸測試：_login_nvr 必須用 AvigilonScanner 真實介面（nvr_config dict +
    user_nonce / user_key kwarg），不能傳 host=... / port=... / username=... /
    password=... 等錯誤 kwarg。

    2026-07-07 user 回報：真實模式按「▶ 預覽快照」噴
    `AvigilonScanner.__init__() got an unexpected keyword argument 'host'`。
    """
    from web import clips_app
    import nvr_scanner as nvr_mod

    # mock AvigilonScanner 與 get_credential（避免真的去連 NVR）
    captured = {}

    class FakeScanner:
        def __init__(self, nvr_config, *, user_nonce, user_key, verify_ssl=False, **kwargs):
            captured["nvr_config"] = nvr_config
            captured["user_nonce"] = user_nonce
            captured["user_key"] = user_key
            captured["verify_ssl"] = verify_ssl
            captured["extra_kwargs"] = kwargs

        def login(self):
            return "FAKE-TOKEN-123"

    # patch 在 nvr_scanner 模組（_login_nvr 內 `from nvr_scanner import AvigilonScanner` 會拿到）
    monkeypatch.setattr(nvr_mod, "AvigilonScanner", FakeScanner)
    monkeypatch.setattr(nvr_mod, "get_credential",
                        lambda env_var, prompt, *, hide=False: f"<{env_var}>")

    nvr_row = {
        "nvr_id": "TEST-NVR",
        "name": "Test NVR",
        "host": "10.0.0.1",
        "port": 8443,
        "username": "admin",
        "password": "secret",
        "verify_ssl": 0,
    }
    token = clips_app._login_nvr(nvr_row)
    assert token == "FAKE-TOKEN-123"

    # 斷言呼叫介面正確
    assert captured["nvr_config"]["host"] == "10.0.0.1"
    assert captured["nvr_config"]["port"] == 8443
    assert captured["user_nonce"] == "<AVIGILON_USER_NONCE>"
    assert captured["user_key"] == "<AVIGILON_USER_KEY>"
    # 不能傳入 host/port/username/password 當 kwarg（會 TypeError）
    forbidden = {"host", "port", "username", "password"}
    leaked = forbidden & set(captured["extra_kwargs"].keys())
    assert not leaked, f"_login_nvr 傳了錯誤的 kwarg: {leaked}"


def test_login_nvr_source_uses_avigilon_scanner_correctly():
    """源碼層 grep 斷言：_login_nvr 不能再傳 host= / username= / password= kwarg。"""
    import inspect
    from web import clips_app

    src = inspect.getsource(clips_app._login_nvr)
    # 不能再出現這些錯誤的 kwarg 呼叫
    for bad_kw in ("host=nvr_row", "username=nvr_row", "password=nvr_row"):
        assert bad_kw not in src, (
            f"_login_nvr 還在傳 {bad_kw}！AvigilonScanner 真實介面不收這些 kwarg"
        )
    # 必須用新介面
    assert "user_nonce=" in src and "user_key=" in src, (
        "_login_nvr 沒傳 user_nonce / user_key；AvigilonScanner 必填"
    )


# === /clips/snapshots 支援 ?camera_ids=A,B,C 多選過濾（2026-07-07） ===

def test_clips_snapshots_filter_by_camera_ids(seeded_clips_app):
    """?camera_ids=A,B → 只回 A、B 兩台（不抓其他相機，節省頻寬 + login）。"""
    app, _ = seeded_clips_app
    with app.test_client() as c:
        # 拿 seeded_clips_app 的 3 台 device_id
        r0 = c.get("/clips/cameras?nvr_id=1")
        cams = r0.get_json()
        all_ids = [cm["device_id"] for cm in cams]
        assert len(all_ids) == 3
        # 只勾前 2 台
        keep = ",".join(all_ids[:2])
        r = c.get(f"/clips/snapshots?nvr_id=1&t=2026-07-06T12:00:00Z&camera_ids={keep}")
        assert r.status_code == 200
        data = r.get_json()
        assert data["camera_count"] == 2
        returned_ids = {s["camera_id"] for s in data["snapshots"]}
        assert returned_ids == set(all_ids[:2])


def test_clips_snapshots_filter_camera_ids_invalid_falls_back_to_all(seeded_clips_app):
    """?camera_ids=不存在的id → 過濾後是空集合，後端應回空 snapshots（不 500）。"""
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=1&t=2026-07-06T12:00:00Z&camera_ids=DOES_NOT_EXIST")
        assert r.status_code == 200
        data = r.get_json()
        assert data["camera_count"] == 0
        assert data["snapshots"] == []


def test_clips_snapshots_no_filter_returns_all(seeded_clips_app):
    """沒帶 camera_ids → 維持向後相容，回全部 3 台。"""
    app, _ = seeded_clips_app
    with app.test_client() as c:
        r = c.get("/clips/snapshots?nvr_id=1&t=2026-07-06T12:00:00Z")
        assert r.status_code == 200
        data = r.get_json()
        assert data["camera_count"] == 3


# === UI 微調按鈕 ±5 分鐘回歸測試（2026-07-08 v3） ===

def test_clips_template_has_fine_tune_buttons():
    """回歸：事件時間欄位必須有「前 5 分」「後 5 分」微調按鈕。

    2026-07-08 user 回報：搜尋出來時間差一點點時不想重新選時間，要 ±5 分鐘微調。
    修法：datetime-local 下方加 .fine-tune-group，按 data-fine="-5" / "+5" 觸發。
    2026-07-08 v2：移除「▶ 載入此時間」（同步撥放按鈕已取代）。
    """
    from pathlib import Path
    template = Path("web/templates/clips.html").read_text(encoding="utf-8")

    import re
    # data-fine 屬性兩個值（go 已移除）
    for val in ("-5", "+5"):
        assert f'data-fine="{val}"' in template, (
            f"找不到 data-fine=\"{val}\" 微調按鈕"
        )
    # 按鈕文字必須能看出功能
    assert "前 5 分" in template, "微調按鈕缺「前 5 分」文字"
    assert "後 5 分" in template, "微調按鈕缺「後 5 分」文字"
    # handler 必須存在
    assert "button[data-fine]" in template, (
        "找不到 button[data-fine] event handler"
    )


# === UI 2×2 同步撥放視窗回歸測試（2026-07-08 v4） ===

def test_clips_template_has_2x2_sync_play_section():
    """回歸：必須有「同步撥放視窗」section（2×2 grid + ⏮/⏭ 切段 + 全部關閉）。

    2026-07-08 user 加：希望 2×2 格子同時撥放最多 4 台相機。
    修法：grid + 4 slot state + 切段按鈕同步。
    """
    from pathlib import Path
    template = Path("web/templates/clips.html").read_text(encoding="utf-8")

    # 1. HTML 必須有 #syncPlaySection / #syncGrid / #syncCenterLabel
    assert 'id="syncPlaySection"' in template, "找不到 #syncPlaySection 容器"
    assert 'id="syncGrid"' in template, "找不到 #syncGrid 容器"
    assert 'id="syncCenterLabel"' in template, "找不到 #syncCenterLabel 中心時間標籤"
    assert 'id="syncPlayBtn"' in template, "找不到 #syncPlayBtn（觸發按鈕）"
    assert 'id="syncPrevBtn"' in template, "找不到 #syncPrevBtn（⏮ 上一段）"
    assert 'id="syncNextBtn"' in template, "找不到 #syncNextBtn（⏭ 下一段）"
    assert 'id="syncCloseAllBtn"' in template, "找不到 #syncCloseAllBtn（⏹ 全部關閉）"

    # 2. JS 必須有 syncSlots state + startSyncPlay / shiftSyncCenter / closeAllSyncSlots
    assert "syncSlots" in template, "找不到 syncSlots state"
    assert "startSyncPlay" in template, "找不到 startSyncPlay 函式"
    assert "shiftSyncCenter" in template, "找不到 shiftSyncCenter（切段）"
    assert "closeAllSyncSlots" in template, "找不到 closeAllSyncSlots（全部關閉）"

    # 3. 必須有 2x2 grid 結構（col-12 col-md-6 → md+ 一排 2）
    assert "col-12 col-md-6" in template, "找不到 2×2 grid class（col-md-6）"


def test_clips_template_caps_4_cameras():
    """回歸：相機勾選上限 4 台（全選按鈕也要遵守）。"""
    from pathlib import Path
    template = Path("web/templates/clips.html").read_text(encoding="utf-8")

    # 全選 handler 必須 slice(0, 4)
    assert "boxes.slice(0, 4)" in template, (
        "全選按鈕沒限縮到 4 台"
    )
    # change handler 必須有 checkedCount > 4 → 取消最早
    assert "checkedCount > 4" in template, (
        "change handler 沒檢查上限"
    )
