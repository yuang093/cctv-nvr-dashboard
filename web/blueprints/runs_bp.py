"""Week 6 #017 — runs_bp。

URL prefix: `/runs`, `/reports`, `/query`
路由（5 條）：
    `/runs`                                GET  跑列表
    `/runs/<int:run_id>`                   GET  跑詳情
    `/reports`                             GET  報告列表
    `/reports/download/<int:run_id>`       GET  下載歷史 PDF
    `/query`                               GET/POST  ad-hoc 唯讀 SQL

Stage A stub。
"""
from __future__ import annotations

from flask import Blueprint

runs_bp = Blueprint("runs", __name__)
