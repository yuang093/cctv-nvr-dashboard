"""Week 6 #017 — devices_bp。

URL prefix: `/devices`, `/health`, `/trends`, `/events`, `/wall`
路由（10 條）：
    `/wall`                          GET   相機牆視覺化
    `/devices`                       GET   跨 NVR 設備總覽
    `/devices/discover`              GET/POST  CIDR 探索
    `/devices/discover/<int:session_id>`  GET  探索結果
    `/devices/<device_id>`           GET   單台 cam 詳情
    `/health/cameras/<device_id>`    GET   cam 健康歷史
    `/trends`                        GET   趨勢 sparkline（Spec G）
    `/events`                        GET   事件列表（Phase 1 status 篩選）
    `/abnormal`                      GET   故障相機彙總
    `/abnormal/export.pdf`           GET   故障 PDF

Stage A stub。
"""
from __future__ import annotations

from flask import Blueprint

devices_bp = Blueprint("devices", __name__)
