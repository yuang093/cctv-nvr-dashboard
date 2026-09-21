"""
scripts/archive_old_partitions.py
=================================
Week 4 Issue #011：歸檔腳本 CLI 入口。

用法：
    python scripts/archive_old_partitions.py --db nvr_scan.db --dry-run
    python scripts/archive_old_partitions.py --db nvr_scan.db --archive-dir ./archives
    python scripts/archive_old_partitions.py --db nvr_scan.db --keep-months 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 讓 script 可直接被 `python scripts/...` 執行，匯入專案模組
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.archive_partitions import list_cold_partitions, run_archive_pass


def main() -> int:
    p = argparse.ArgumentParser(
        description="歸檔 events 舊分區表（gzip + DROP + VACUUM）"
    )
    p.add_argument("--db", default="./nvr_scan.db", help="SQLite 檔路徑")
    p.add_argument(
        "--archive-dir",
        default="./archives",
        help=".sql.gz 輸出目錄（預設 ./archives）",
    )
    p.add_argument(
        "--hot-window",
        type=int,
        default=4,
        help="view 內含的月份數（含當月）；預設 4 = 90 天滑動窗",
    )
    p.add_argument(
        "--keep-months",
        type=int,
        default=0,
        help="保留最近 N 個月 cold table 不歸檔（安全緩衝；預設 0）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出將被歸檔的表，不實際執行",
    )
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f"[FATAL] 找不到 DB：{args.db}", file=sys.stderr)
        return 2

    if args.dry_run:
        cold = list_cold_partitions(args.db, hot_window=args.hot_window)
        # keep_months 安全緩衝
        if args.keep_months > 0 and len(cold) > args.keep_months:
            would_archive = cold[: -args.keep_months]
            would_keep = cold[-args.keep_months :]
        else:
            would_archive = cold
            would_keep = []
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "would_archive": would_archive,
                    "would_keep_for_safety": would_keep,
                    "hot_window": args.hot_window,
                    "keep_months": args.keep_months,
                    "archive_dir": args.archive_dir,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    summary = run_archive_pass(
        db_path=args.db,
        archive_dir=args.archive_dir,
        hot_window=args.hot_window,
        keep_months=args.keep_months,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
