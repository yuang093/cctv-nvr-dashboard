"""Week 6 #018 — coverage_bp。

URL prefix: `/clips/coverage`
路由（2 條）：
    `/clips/coverage`        GET  熱區頁（coverage.html）
    `/clips/coverage/data`   GET  熱區 JSON（Spec F 含 NVR MPD 拉取）

依賴：web.coverage.fetch_coverage_from_nvr、nvr_scanner.AvigilonScanner、web.db.get_nvr / list_cameras_for_nvr。
"""
from __future__ import annotations

from flask import Blueprint

coverage_bp = Blueprint("clips_coverage", __name__, url_prefix="/clips/coverage")
