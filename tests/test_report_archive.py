"""
tests/test_report_archive.py
============================
PDF 報告歸檔模組測試。

涵蓋：
    - make_filename / parse_filename round-trip
    - save_report 寫檔正確 + 自動建 reports/ 目錄
    - list_reports 排序（新的在前）+ JOIN scan_runs 補 status/abnormal
    - list_reports 跳過格式不符的檔
    - list_reports reports/ 不存在回空 list
    - find_report 依 run_id 找最新一份
    - find_report 不存在回 None
    - GET /reports 200 + 渲染 items
    - GET /reports/download/<run_id> 200 + application/pdf
    - GET /reports/download/<不存在的 id> 404
    - _archive_current_report helper（直接呼叫）寫檔
"""

from __future__ import annotations

import gc
import tempfile
import time
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web import report_archive
from web.app import create_app


# === Fixtures ===


@pytest.fixture
def db_path():
    """空 DB 路徑。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    SqliteWriter(path)  # init schema
    yield path
    # 連 reports/ 也清掉
    rdir = Path(path).resolve().parent / "reports"
    if rdir.exists():
        for f in rdir.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            rdir.rmdir()
        except OSError:
            pass
    try:
        Path(path).unlink()
    except OSError:
        pass


@pytest.fixture
def app(db_path):
    """Flask app。"""
    a = create_app(db_path=db_path)
    a.config["TESTING"] = True
    yield a
    del a
    gc.collect()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_scan_run(
    db_path: str, run_id: int, status: str = "ok", abnormal: int = 0
) -> int:
    """塞一個 scan_run（直接寫 DB，不走 SqliteWriter 因為 _current_scan_run 狀態麻煩）。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO scan_runs (id, started_at, finished_at, status, "
            "total_nvrs, ok_nvrs, failed_nvrs, abnormal_cameras) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                f"2026-07-07 17:30:{run_id:02d}",
                f"2026-07-07 17:31:{run_id:02d}",
                status,
                1,
                1,
                0,
                abnormal,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


# === 純函式測試 ===


def test_make_filename_includes_run_id_and_timestamp():
    fname = report_archive.make_filename(
        13,
        when_ts=time.mktime(time.strptime("2026-07-07 17:30:12", "%Y-%m-%d %H:%M:%S")),
    )
    assert fname == "report_run13_20260707_173012.pdf"


def test_parse_filename_roundtrip():
    fname = "report_run13_20260707_173012.pdf"
    p = report_archive.parse_filename(fname)
    assert p is not None
    assert p["run_id"] == 13
    assert p["started_at"] == "2026-07-07 17:30:12"


def test_parse_filename_invalid_returns_none():
    assert report_archive.parse_filename("foo.pdf") is None
    assert report_archive.parse_filename("report_runX_20260707_173012.pdf") is None
    assert (
        report_archive.parse_filename("report_run13_20260707_1730.pdf") is None
    )  # 缺秒


# === reports_dir + save_report ===


def test_reports_dir_auto_creates(db_path):
    rdir = report_archive.reports_dir(db_path)
    assert rdir.exists()
    assert rdir.is_dir()
    assert rdir.name == "reports"


def test_save_report_creates_file(db_path):
    fpath = report_archive.save_report(db_path, run_id=13, pdf_bytes=b"%PDF-fake")
    assert fpath.exists()
    assert fpath.read_bytes() == b"%PDF-fake"
    assert fpath.name.startswith("report_run13_")
    assert fpath.name.endswith(".pdf")


def test_save_report_explicit_when_ts(db_path):
    when = time.mktime(time.strptime("2026-07-01 09:00:00", "%Y-%m-%d %H:%M:%S"))
    fpath = report_archive.save_report(db_path, run_id=42, pdf_bytes=b"x", when_ts=when)
    assert "20260701" in fpath.name
    assert "090000" in fpath.name


# === list_reports ===


def test_list_reports_empty_when_no_dir(db_path):
    # 連 reports/ 都不讓它被建立
    items = report_archive.list_reports(db_path)
    # save_report 沒呼叫過就不會建 reports/
    # 但 reports_dir() 會建，所以這裡只測「沒有檔」回空 list
    rdir = Path(db_path).resolve().parent / "reports"
    if rdir.exists():
        items = report_archive.list_reports(db_path)
    assert items == []


def test_list_reports_skips_invalid_files(db_path):
    # 塞合法 + 不合法檔
    report_archive.save_report(db_path, run_id=1, pdf_bytes=b"a")
    rdir = report_archive.reports_dir(db_path)
    (rdir / "garbage.pdf").write_bytes(b"junk")
    (rdir / "README.txt").write_text("not a pdf")

    items = report_archive.list_reports(db_path)
    assert len(items) == 1
    assert items[0]["run_id"] == 1


def test_list_reports_joins_scan_runs(db_path):
    report_archive.save_report(db_path, run_id=10, pdf_bytes=b"a")
    report_archive.save_report(db_path, run_id=11, pdf_bytes=b"b")
    _seed_scan_run(db_path, run_id=10, status="ok", abnormal=2)
    _seed_scan_run(db_path, run_id=11, status="failed", abnormal=5)

    items = report_archive.list_reports(db_path)
    assert len(items) == 2
    by_run = {it["run_id"]: it for it in items}
    assert by_run[10]["run_status"] == "ok"
    assert by_run[10]["abnormal_cameras"] == 2
    assert by_run[11]["run_status"] == "failed"
    assert by_run[11]["abnormal_cameras"] == 5


def test_list_reports_no_scan_run_in_db(db_path):
    """檔案存在但 DB 沒這 run_id → status/abnormal 為 None。"""
    report_archive.save_report(db_path, run_id=999, pdf_bytes=b"x")
    items = report_archive.list_reports(db_path)
    assert len(items) == 1
    assert items[0]["run_status"] is None
    assert items[0]["abnormal_cameras"] is None


def test_list_reports_sorted_newest_first(db_path):
    # 用顯式 when_ts 製造不同時間
    t1 = time.mktime(time.strptime("2026-07-07 10:00:00", "%Y-%m-%d %H:%M:%S"))
    t2 = time.mktime(time.strptime("2026-07-07 15:00:00", "%Y-%m-%d %H:%M:%S"))
    t3 = time.mktime(time.strptime("2026-07-07 12:00:00", "%Y-%m-%d %H:%M:%S"))
    report_archive.save_report(db_path, run_id=1, pdf_bytes=b"a", when_ts=t1)
    report_archive.save_report(db_path, run_id=2, pdf_bytes=b"b", when_ts=t2)
    report_archive.save_report(db_path, run_id=3, pdf_bytes=b"c", when_ts=t3)

    items = report_archive.list_reports(db_path)
    assert [it["run_id"] for it in items] == [2, 3, 1]


# === find_report ===


def test_find_report_returns_latest_for_run_id(db_path):
    t1 = time.mktime(time.strptime("2026-07-07 10:00:00", "%Y-%m-%d %H:%M:%S"))
    t2 = time.mktime(time.strptime("2026-07-07 15:00:00", "%Y-%m-%d %H:%M:%S"))
    report_archive.save_report(db_path, run_id=5, pdf_bytes=b"old", when_ts=t1)
    report_archive.save_report(db_path, run_id=5, pdf_bytes=b"new", when_ts=t2)

    fpath = report_archive.find_report(db_path, run_id=5)
    assert fpath is not None
    assert fpath.read_bytes() == b"new"


def test_find_report_missing_returns_none(db_path):
    report_archive.save_report(db_path, run_id=1, pdf_bytes=b"a")
    assert report_archive.find_report(db_path, run_id=999) is None


# === Flask routes ===


def test_reports_list_empty(client):
    resp = client.get("/reports")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "歷史" in body or "沒有" in body or "歸檔" in body


def test_reports_list_shows_items(client, db_path):
    report_archive.save_report(db_path, run_id=7, pdf_bytes=b"%PDF-7")
    _seed_scan_run(db_path, run_id=7, status="partial", abnormal=3)
    resp = client.get("/reports")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "#7" in body
    assert "partial" in body


def test_reports_download_success(client, db_path):
    report_archive.save_report(db_path, run_id=8, pdf_bytes=b"%PDF-fake-content")
    resp = client.get("/reports/download/8")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/pdf")
    # send_file with as_attachment 會設 Content-Disposition
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    assert "report_run8_" in resp.headers.get("Content-Disposition", "")


def test_reports_download_missing_404(client):
    resp = client.get("/reports/download/999")
    assert resp.status_code == 404


def test_reports_download_invalid_id_not_int(client):
    # /reports/download/abc 不會 match <int:run_id> → 404
    resp = client.get("/reports/download/abc")
    assert resp.status_code == 404


# === _archive_current_report helper ===


def test_archive_current_report_writes_file(app, db_path):
    """直接呼叫 helper：寫檔 + JOIN 正常。"""
    _seed_scan_run(db_path, run_id=20, status="ok", abnormal=1)
    from web.app import _archive_current_report

    _archive_current_report(db_path, {"id": 20})

    fpath = report_archive.find_report(db_path, run_id=20)
    assert fpath is not None
    # 真 PDF 開頭是 %PDF
    assert fpath.read_bytes()[:4] == b"%PDF"
