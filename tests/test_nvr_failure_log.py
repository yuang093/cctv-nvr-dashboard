"""
tests/test_nvr_failure_log.py
=============================
NVR 連線失敗記錄全鏈路測試。

涵蓋：
    - migration：SqliteWriter 自動建表 + 索引（idempotent）
    - SqliteWriter.log_nvr_failure：寫入 + 回傳 id + nullable nvr_internal_id
    - web.db.get_nvr_failures_for_run：基本查詢、空結果
    - dashboard「最近五次」表格 NVR 連線欄顏色 badge（4 種狀態）
    - run_detail NVR 失敗明細區段顯示
"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest

from db.sqlite_writer import SqliteWriter
from web.app import create_app


# === Fixtures ===


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    SqliteWriter(path)
    yield path
    try:
        Path(path).unlink()
    except OSError:
        pass


@pytest.fixture
def app(db_path):
    a = create_app(db_path=db_path)
    a.config["TESTING"] = True
    yield a
    del a
    gc.collect()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_nvr_and_run(
    db_path: str, nvr_count: int = 1
) -> tuple[SqliteWriter, int, list[int]]:
    """塞 NVR + 1 個 scan_run，回傳 (writer, run_id, [internal_ids])。

    回傳 writer 讓 caller reuse 同一條 connection 繼續寫新 run
    （SqliteWriter 在 transaction 內寫入，外連線看不到 uncommitted data）。
    """
    w = SqliteWriter(db_path)
    internal_ids = []
    for i in range(nvr_count):
        nid = w.upsert_nvr(
            {
                "id": f"NVR-{i+1}",
                "name": f"NVR{i+1}",
                "host": "1.1.1.1",
                "port": 8443,
                "username": "u",
                "password": "p",
            }
        )
        internal_ids.append(nid)
    run_id = w.begin_scan_run("2026-07-07T10:00:00Z")
    w.finish_scan_run(
        run_id,
        finished_at="2026-07-07T10:01:00Z",
        status="partial",
        stats={"total_nvrs": nvr_count, "ok_nvrs": nvr_count - 1, "failed_nvrs": 1},
    )
    return w, run_id, internal_ids


# === Migration 測試 ===


def test_migration_creates_nvr_failure_log(db_path):
    """SqliteWriter.__init__ 自動建表。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "nvr_failure_log" in tables
        # 索引
        idx = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='nvr_failure_log'"
            ).fetchall()
        ]
        assert "idx_nvr_failure_log_scan_run_id" in idx
        assert "idx_nvr_failure_log_nvr_id_failed_at" in idx
    finally:
        conn.close()


def test_migration_idempotent(db_path):
    """SqliteWriter 開兩次不會壞。"""
    SqliteWriter(db_path)  # 第二次 init
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        # 寫一筆確認表可用
        conn.execute(
            "INSERT INTO scan_runs (started_at, status) VALUES (?, ?)",
            ("2026-07-07T00:00:00Z", "failed"),
        )
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO nvr_failure_log "
            "(scan_run_id, nvr_id, nvr_name, error_type, error_message, failed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, "X", "X-Name", "Timeout", "timeout", "2026-07-07T00:00:00Z"),
        )
        conn.commit()
        rows = conn.execute("SELECT COUNT(*) FROM nvr_failure_log").fetchone()
        assert rows[0] == 1
    finally:
        conn.close()


# === SqliteWriter.log_nvr_failure ===


def test_log_nvr_failure_basic(db_path):
    w, _, [nid] = _seed_nvr_and_run(db_path, nvr_count=1)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    new_id = w.log_nvr_failure(
        new_run_id,
        "NVR-1",
        "NVR1",
        error_type="ConnectionError",
        error_message="Connection refused",
        nvr_internal_id=nid,
    )
    assert isinstance(new_id, int)
    assert new_id > 0


def test_log_nvr_failure_with_null_internal_id(db_path):
    """upsert 失敗時 nvr_internal_id=None 不應炸。"""
    w, _, _ = _seed_nvr_and_run(db_path)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    new_id = w.log_nvr_failure(
        new_run_id,
        "BAD-NVR",
        "壞的 NVR",
        error_type="ValueError",
        error_message="invalid host",
        nvr_internal_id=None,
    )
    assert new_id > 0


def test_log_nvr_failure_persists_in_transaction(db_path):
    """log_nvr_failure 寫入後，query 馬上看得到（finish commit 後）。"""
    w, _, [nid] = _seed_nvr_and_run(db_path)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    w.log_nvr_failure(
        new_run_id,
        "NVR-1",
        "NVR1",
        error_type="Timeout",
        error_message="read timed out",
        nvr_internal_id=nid,
    )
    w.finish_scan_run(
        new_run_id,
        finished_at="2026-07-07T11:01:00Z",
        status="failed",
        stats={"failed_nvrs": 1},
    )

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM nvr_failure_log WHERE scan_run_id = ?",
            (new_run_id,),
        ).fetchone()[0]
        assert cnt == 1
    finally:
        conn.close()


def test_log_nvr_failure_failed_at_custom(db_path):
    """自訂 failed_at 時間。"""
    w, _, [nid] = _seed_nvr_and_run(db_path)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    w.log_nvr_failure(
        new_run_id,
        "NVR-1",
        "NVR1",
        error_type="AuthError",
        error_message="bad password",
        nvr_internal_id=nid,
        failed_at="2026-06-01T08:00:00Z",
    )
    w.finish_scan_run(
        new_run_id,
        finished_at="2026-07-07T11:01:00Z",
        status="failed",
        stats={"failed_nvrs": 1},
    )

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT failed_at FROM nvr_failure_log WHERE scan_run_id = ?",
            (new_run_id,),
        ).fetchone()
        assert row[0] == "2026-06-01T08:00:00Z"
    finally:
        conn.close()


def test_log_nvr_failure_wrong_run_id_raises(db_path):
    """active scan_run 與傳入 run_id 不符時 RuntimeError。"""
    w, old_run_id, _ = _seed_nvr_and_run(db_path)
    w.begin_scan_run("2026-07-07T12:00:00Z")  # 新 run 變 active
    with pytest.raises(RuntimeError, match="scan_run_id 不符"):
        w.log_nvr_failure(
            old_run_id,  # ← 舊的
            "NVR-1",
            "NVR1",
            error_type="X",
            error_message="x",
        )


# === web.db.get_nvr_failures_for_run ===


def test_get_nvr_failures_for_run_returns_list(db_path):
    w, _, [nid] = _seed_nvr_and_run(db_path)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    w.log_nvr_failure(
        new_run_id,
        "NVR-1",
        "NVR1",
        error_type="ConnectionError",
        error_message="refused",
        nvr_internal_id=nid,
    )
    w.finish_scan_run(
        new_run_id,
        finished_at="2026-07-07T11:01:00Z",
        status="failed",
        stats={"failed_nvrs": 1},
    )

    from web import db as webdb

    failures = webdb.get_nvr_failures_for_run(db_path, new_run_id)
    assert len(failures) == 1
    assert failures[0]["nvr_id"] == "NVR-1"
    assert failures[0]["nvr_name"] == "NVR1"
    assert failures[0]["error_type"] == "ConnectionError"
    assert "refused" in failures[0]["error_message"]


def test_get_nvr_failures_for_run_empty(db_path):
    _, first_run_id, _ = _seed_nvr_and_run(db_path)  # 沒 log_nvr_failure
    from web import db as webdb

    failures = webdb.get_nvr_failures_for_run(db_path, first_run_id)
    assert failures == []


def test_get_nvr_failures_for_run_orders_by_id(db_path):
    """多筆失敗時按 id 順序（寫入順序）。"""
    w, _, _ = _seed_nvr_and_run(db_path)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    w.log_nvr_failure(new_run_id, "NVR-A", "A", error_type="X", error_message="1")
    w.log_nvr_failure(new_run_id, "NVR-B", "B", error_type="X", error_message="2")
    w.log_nvr_failure(new_run_id, "NVR-C", "C", error_type="X", error_message="3")
    w.finish_scan_run(
        new_run_id,
        finished_at="2026-07-07T11:01:00Z",
        status="failed",
        stats={"failed_nvrs": 3},
    )

    from web import db as webdb

    failures = webdb.get_nvr_failures_for_run(db_path, new_run_id)
    assert [f["nvr_id"] for f in failures] == ["NVR-A", "NVR-B", "NVR-C"]


# === Dashboard UI 顏色 badge ===


@pytest.mark.parametrize(
    "ok,total,expected_class,expected_marker",
    [
        (3, 3, "bg-success", "✓"),  # 全部通
        (2, 3, "bg-warning", "⚠"),  # 部分通
        (0, 3, "bg-danger", "✗"),  # 全失敗
        (0, 0, "bg-secondary", "—"),  # 無設定
    ],
)
def test_dashboard_nvr_badge(
    client, db_path, ok, total, expected_class, expected_marker
):
    """dashboard NVR 連線欄：四種狀態的顏色 + 標記正確。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO scan_runs (started_at, finished_at, status, "
            "total_nvrs, ok_nvrs, failed_nvrs, total_cameras, abnormal_cameras) "
            "VALUES (?, ?, 'partial', ?, ?, ?, 0, 0)",
            ("2026-07-07T10:00:00Z", "2026-07-07T10:01:00Z", total, ok, total - ok),
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert expected_class in body
    assert expected_marker in body
    # 「NVR 連線」欄標題
    assert "NVR 連線" in body


# === Run detail NVR 失敗區段 ===


def test_run_detail_shows_nvr_failures(client, db_path):
    """run_detail 有 nvr_failures 時顯示紅框區段。"""
    w, _, [nid] = _seed_nvr_and_run(db_path)
    new_run_id = w.begin_scan_run("2026-07-07T11:00:00Z")
    w.log_nvr_failure(
        new_run_id,
        "NVR-1",
        "NVR1",
        error_type="ConnectionError",
        error_message="Connection refused",
        nvr_internal_id=nid,
    )
    w.finish_scan_run(
        new_run_id,
        finished_at="2026-07-07T11:01:00Z",
        status="failed",
        stats={"failed_nvrs": 1},
    )

    resp = client.get(f"/runs/{new_run_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "NVR 連線失敗" in body
    assert "NVR1" in body
    assert "ConnectionError" in body
    assert "Connection refused" in body
    # 紅框 class
    assert "border-danger" in body


def test_run_detail_no_nvr_failures_no_section(client, db_path):
    """沒 nvr_failures 時不顯示紅框區段。"""
    _, first_run_id, _ = _seed_nvr_and_run(db_path)  # 沒 log 失敗
    resp = client.get(f"/runs/{first_run_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "NVR 連線失敗" not in body
    assert "border-danger" not in body


# === Runs list 顏色 badge ===


def test_runs_list_nvr_badge(client, db_path):
    """runs_list 頁 NVR 連線欄也吃同樣顏色規則。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO scan_runs (started_at, finished_at, status, "
            "total_nvrs, ok_nvrs, failed_nvrs, total_cameras, abnormal_cameras) "
            "VALUES ('2026-07-07T10:00:00Z', '2026-07-07T10:01:00Z', "
            "'partial', 5, 1, 4, 10, 0)",
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/runs")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # 部分通：黃色 + ⚠
    assert "bg-warning" in body
    assert "⚠" in body
    assert "1 / 5" in body
