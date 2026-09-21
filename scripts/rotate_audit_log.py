"""
scripts/rotate_audit_log.py
===========================
Week 5 Issue #015：audit log daily rotation CLI。

每日凌晨 04:00 跑（run_worker 三平台守門）：
1. 超過 90 天的 audit_log rows → dump_and_compress → DELETE
2. 沿用 db.archive_partitions.dump_and_compress 介面

用法：
    python scripts/rotate_audit_log.py --db nvr_scan.db --dry-run
    python scripts/rotate_audit_log.py --db nvr_scan.db --archive-dir ./archives/audit
    python scripts/rotate_audit_log.py --db nvr_scan.db --retention-days 90
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from audit.log import rotate_audit_log
from db.archive_partitions import dump_and_compress


def main() -> int:
    p = argparse.ArgumentParser(
        description="audit log daily rotation (gzip 封存 + DELETE)",
    )
    p.add_argument("--db", default="./nvr_scan.db", help="SQLite 檔路徑")
    p.add_argument(
        "--archive-dir",
        default="./archives/audit",
        help=".sql.gz 輸出目錄（預設 ./archives/audit）",
    )
    p.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="保留天數；超過此天數的 audit_log 會被歸檔（預設 90）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出將被歸檔的筆數，不實際執行",
    )
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f"[FATAL] 找不到 DB：{args.db}", file=sys.stderr)
        return 2

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=args.retention_days)
    ).isoformat()

    if args.dry_run:
        # 只計算不刪除
        conn = sqlite3.connect(args.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE created_at < ?",
                (cutoff,),
            ).fetchone()[0]
        finally:
            conn.close()
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "would_delete": count,
                    "retention_days": args.retention_days,
                    "cutoff": cutoff,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    # 1. 先 dump 將被刪除的 rows → .sql.gz
    archive_path = (
        Path(args.archive_dir) / f"audit_log_{date.today().isoformat()}.sql.gz"
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    dump_and_compress(
        args.db,
        "audit_log",
        str(archive_path.parent),
        suffix=archive_path.name,
    )

    # 2. 刪除超過 retention_days 的 rows
    deleted = rotate_audit_log(args.db, args.retention_days)
    print(
        json.dumps(
            {
                "deleted": deleted,
                "archived_to": str(archive_path),
                "retention_days": args.retention_days,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
