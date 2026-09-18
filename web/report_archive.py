"""故障報告 PDF 歸檔模組。

設計：
    - 每次 batch_scan 完成自動存檔到 ./reports/ 目錄（與 nvr_scan.db 同層）
    - 檔名格式：report_run<run_id>_<YYYYMMDD>_<HHMMSS>.pdf
      （run_id 在前方便排序；timestamp 在後方便閱讀）
    - 歷史列表：解析檔名 → JOIN scan_runs 取 abnormal_cameras / status
    - 全部保留（v1 簡化策略；一份 PDF ~50KB 不占空間）

依賴：
    - 報表內容（groups / topic_zh / last_run）來自 web.db
    - PDF bytes 由 web.app._build_abnormal_pdf 生成（共用同一份版面）
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 檔名格式：report_run<run_id>_<YYYYMMDD>_<HHMMSS>.pdf
# 例：report_run13_20260707_173012.pdf
_FILENAME_RE = re.compile(
    r"^report_run(?P<run_id>\d+)_(?P<date>\d{8})_(?P<time>\d{6})\.pdf$"
)


def reports_dir(db_path: str) -> Path:
    """歸檔目錄路徑（與 DB 同層）。目錄不存在時 lazy 建。"""
    p = Path(db_path).resolve().parent / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def make_filename(run_id: int, when_ts: float | None = None) -> str:
    """產生歸檔檔名。

    run_id: scan_runs.id（必填）
    when_ts: epoch seconds（預設 = 現在，用於檔名時間戳；
             若要與 scan_run 的 started_at 對齊，傳入該時間）
    """
    ts = when_ts if when_ts is not None else time.time()
    return time.strftime(
        f"report_run{int(run_id)}_%Y%m%d_%H%M%S.pdf", time.localtime(ts)
    )


def save_report(
    db_path: str,
    run_id: int,
    pdf_bytes: bytes,
    when_ts: float | None = None,
) -> Path:
    """存檔到 ./reports/。回傳實際寫入的 Path。

    - 目錄不存在會自動建
    - 同檔名存在會覆蓋（理論上 run_id + 秒級 timestamp 不會撞）
    """
    rdir = reports_dir(db_path)
    fname = make_filename(run_id, when_ts)
    fpath = rdir / fname
    fpath.write_bytes(pdf_bytes)
    logger.info("report archived: %s (%d bytes)", fpath, len(pdf_bytes))
    return fpath


def parse_filename(fname: str) -> dict[str, Any] | None:
    """解析檔名回傳 dict；格式不符回傳 None。

    回傳：{"run_id": int, "started_at": "YYYY-MM-DD HH:MM:SS", "epoch": float}
    """
    m = _FILENAME_RE.match(fname)
    if not m:
        return None
    run_id = int(m["run_id"])
    date_str = m["date"]
    time_str = m["time"]
    # 組合成本地時區的 struct_time（給 time.mktime 算 epoch）
    try:
        st = time.strptime(
            f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} "
            f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}",
            "%Y-%m-%d %H:%M:%S",
        )
        epoch = time.mktime(st)
        started_at = time.strftime("%Y-%m-%d %H:%M:%S", st)
    except ValueError:
        return None
    return {"run_id": run_id, "started_at": started_at, "epoch": epoch}


def list_reports(db_path: str) -> list[dict[str, Any]]:
    """列出所有歸檔報告，按時間倒序（新 → 舊）。

    每筆 dict：
        - filename: 檔名
        - run_id: scan_run id（從檔名解析）
        - started_at: 產生時間字串（從檔名）
        - size_bytes: 檔案大小
        - mtime: 最後修改 epoch
        - run_status: 從 scan_runs JOIN（"ok"/"partial"/"failed"/None）
        - abnormal_cameras: 從 scan_runs JOIN（int）
    """
    rdir = reports_dir(db_path)
    if not rdir.exists():
        return []

    items: list[dict[str, Any]] = []
    for f in rdir.iterdir():
        if not f.is_file():
            continue
        parsed = parse_filename(f.name)
        if not parsed:
            continue  # 跳過格式不符的（可能是手動放的）
        st = f.stat()
        items.append(
            {
                "filename": f.name,
                "run_id": parsed["run_id"],
                "started_at": parsed["started_at"],
                "epoch": parsed["epoch"],
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,
            }
        )

    # 從 DB 補上 status / abnormal_cameras
    if items:
        try:
            run_ids = sorted({it["run_id"] for it in items})
            placeholders = ",".join("?" * len(run_ids))
            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    f"SELECT id, status, abnormal_cameras FROM scan_runs "
                    f"WHERE id IN ({placeholders})",
                    run_ids,
                ).fetchall()
                meta = {r[0]: {"status": r[1], "abnormal_cameras": r[2]} for r in rows}
            finally:
                conn.close()
            for it in items:
                m = meta.get(it["run_id"])
                if m:
                    it["run_status"] = m["status"]
                    it["abnormal_cameras"] = m["abnormal_cameras"]
                else:
                    it["run_status"] = None
                    it["abnormal_cameras"] = None
        except sqlite3.Error as e:
            logger.warning("list_reports: scan_runs JOIN failed: %s", e)
            for it in items:
                it["run_status"] = None
                it["abnormal_cameras"] = None

    items.sort(key=lambda x: x["epoch"], reverse=True)
    return items


def find_report(db_path: str, run_id: int) -> Path | None:
    """依 run_id 找歸檔檔案；多份（同 run_id 不同秒）回最新的。"""
    rdir = reports_dir(db_path)
    if not rdir.exists():
        return None
    candidates = []
    for f in rdir.iterdir():
        parsed = parse_filename(f.name)
        if parsed and parsed["run_id"] == int(run_id):
            candidates.append((f.stat().st_mtime, f))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]
