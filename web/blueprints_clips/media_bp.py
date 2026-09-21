"""Week 6 #018 — media_bp。

URL prefix: `/clips`
路由（5 條）：
    `/clips/nvrs`        GET  NVR dropdown JSON
    `/clips/cameras`     GET  單 NVR cameras JSON
    `/clips/snapshots`   GET  並行抓 snapshots
    `/clips/fetch`       POST 同步單段 clip bytes
    `/clips/fetch_sync`  POST 多 cam 同步 multipart

依賴：web.clip_retrieval（Protocol + Mpd / Mock）+ MediaApiClient factory。
"""
from __future__ import annotations

from flask import Blueprint

media_bp = Blueprint("clips_media", __name__, url_prefix="/clips")
