"""
web/fleet.py
============
2026-07-29 新功能：/fleet 頁伺服器概覽資料聚合。

對應 spec：docs/superpowers/specs/2026-07-29-fleet-view-design.md
"""
from __future__ import annotations

import logging
import time

from web.db import get_nvrs, get_wall_cameras_with_snapshots

log = logging.getLogger(__name__)

_CACHE: dict = {"db_path": None, "data": None, "ts": 0.0}
_TTL_SECONDS = 30.0


def get_fleet_view(db_path: str, *, force_refresh: bool = False) -> list[dict]:
    """回傳每台 enabled NVR 的概覽 dict list。

    30s TTL in-memory cache。force_refresh=True 強制重算（測試用）。
    單台 NVR 例外不中斷整批；該台 status='unknown'，其他台正常。
    """
    now = time.time()
    if (
        not force_refresh
        and _CACHE["db_path"] == db_path
        and _CACHE["data"] is not None
        and now - _CACHE["ts"] < _TTL_SECONDS
    ):
        return _CACHE["data"]

    # get_nvrs() 同時回 id（INTEGER primary key, FK 給 cameras.nvr_id）與 nvr_id（string）。
    # 此處需整數 internal_id 才能餵 get_wall_cameras_with_snapshots(nvr_id=...)
    # 的 WHERE c.nvr_id = ? 過濾；list_enabled_nvrs 的 id 是字串 nvr_id，不適用。
    nvrs = [n for n in get_nvrs(db_path) if n.get("enabled") == 1]
    out: list[dict] = []
    for nvr in nvrs:
        try:
            cams = get_wall_cameras_with_snapshots(
                db_path, filter_kind="all", nvr_id=nvr["id"]
            )
            healthy = sum(1 for c in cams if c["category"] == "online")
            signal_lost = sum(1 for c in cams if c["category"] == "signal_lost")
            no_signal = sum(1 for c in cams if c["category"] == "no_signal")
            total = len(cams)
            if no_signal > 0:
                status = "critical"
            elif signal_lost > 0:
                status = "degraded"
            else:
                status = "ok"
        except Exception as exc:
            log.warning("fleet view failed for nvr_id=%s: %s", nvr["id"], exc)
            healthy = signal_lost = no_signal = total = 0
            status = "unknown"

        out.append({
            "nvr_id": nvr["id"],
            "name": nvr["name"],
            "host": nvr["host"],
            "port": nvr.get("port", 8443),
            "total": total,
            "healthy": healthy,
            "signal_lost": signal_lost,
            "no_signal": no_signal,
            "status": status,
        })

    _CACHE["db_path"] = db_path
    _CACHE["data"] = out
    _CACHE["ts"] = now
    return out


def clear_cache() -> None:
    """測試 / NVR 設定變動後可呼叫清掉 cache。"""
    _CACHE["db_path"] = None
    _CACHE["data"] = None
    _CACHE["ts"] = 0.0
