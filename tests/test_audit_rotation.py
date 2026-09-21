"""Week 5 #015：audit log rotation CLI + dump_and_compress suffix 測試（Task 4）。"""
from __future__ import annotations

import gzip
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def audit_db_with_old_rows(tmp_path: Path) -> str:
    """建立含 audit_log 表 + 一些過期/未過期 rows。"""
    from db.migrations.migrate_create_audit_log import run as migrate_audit

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """
    )
    conn.commit()
    conn.close()
    migrate_audit(db_path)

    # 塞過期與未過期 rows
    conn = sqlite3.connect(db_path)
    old_date = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    new_date = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO audit_log (event, ip, created_at) VALUES (?, ?, ?)",
        ("login", "10.0.0.1", old_date),
    )
    conn.execute(
        "INSERT INTO audit_log (event, ip, created_at) VALUES (?, ?, ?)",
        ("login", "10.0.0.2", old_date),
    )
    conn.execute(
        "INSERT INTO audit_log (event, ip, created_at) VALUES (?, ?, ?)",
        ("login", "10.0.0.3", new_date),
    )
    conn.commit()
    conn.close()
    return db_path


def test_dump_and_compress_with_suffix(audit_db_with_old_rows: str, tmp_path: Path) -> None:
    """dump_and_compress 接受自訂 suffix（audit_log_YYYY-MM-DD.sql.gz）。"""
    from db.archive_partitions import dump_and_compress

    archive_dir = tmp_path / "archives" / "audit"
    gz_path = dump_and_compress(
        audit_db_with_old_rows,
        "audit_log",
        str(archive_dir),
        suffix="audit_log_2026-09-22.sql.gz",
    )
    assert gz_path.endswith("audit_log_2026-09-22.sql.gz")
    assert Path(gz_path).exists()
    with gzip.open(gz_path, "rt") as f:
        content = f.read()
    assert "CREATE TABLE audit_log" in content
    assert "INSERT INTO" in content and "audit_log" in content
    assert "10.0.0.1" in content


def test_rotate_audit_log_cli_dry_run(
    audit_db_with_old_rows: str, tmp_path: Path
) -> None:
    """CLI --dry-run 列出將被歸檔筆數，不實際刪除。"""
    script = (
        Path(__file__).resolve().parent.parent / "scripts" / "rotate_audit_log.py"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            audit_db_with_old_rows,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["dry_run"] is True
    assert out["would_delete"] == 2  # 兩條 91 天前的
    # 確認 rows 還在
    conn = sqlite3.connect(audit_db_with_old_rows)
    count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    conn.close()
    assert count == 3


def test_rotate_audit_log_cli_full(
    audit_db_with_old_rows: str, tmp_path: Path
) -> None:
    """CLI 完整執行：歸檔 .sql.gz + DELETE 過期 rows。"""
    archive_dir = tmp_path / "archives" / "audit"
    script = (
        Path(__file__).resolve().parent.parent / "scripts" / "rotate_audit_log.py"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            audit_db_with_old_rows,
            "--archive-dir",
            str(archive_dir),
            "--retention-days",
            "90",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["deleted"] == 2

    # 1. archive 檔存在
    archive_files = list(archive_dir.glob("audit_log_*.sql.gz"))
    assert len(archive_files) == 1
    # 2. 只剩 1 筆（未過期的）
    conn = sqlite3.connect(audit_db_with_old_rows)
    count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    conn.close()
    assert count == 1


def test_rotate_audit_log_cli_missing_db(tmp_path: Path) -> None:
    """CLI 對不存在的 DB 應回 exit code 2。"""
    script = (
        Path(__file__).resolve().parent.parent / "scripts" / "rotate_audit_log.py"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--db",
            str(tmp_path / "nonexistent.db"),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "FATAL" in proc.stderr
