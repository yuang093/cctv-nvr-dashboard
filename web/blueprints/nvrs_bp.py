"""Week 6 #017 — nvrs_bp。

URL prefix: `/nvrs`
路由（11 條）：
    `/nvrs`                                GET   列表
    `/nvrs/<int:internal_id>/toggle`       POST  啟用切換
    `/nvrs/new`                            GET/POST  新增
    `/nvrs/<int:nvr_id>/edit`              GET/POST  編輯
    `/nvrs/<int:nvr_id>/delete`            POST  刪除
    `/nvrs/test-connection`                POST  AJAX 連線測試
    `/nvrs/import`                         GET/POST  批次匯入
    `/nvrs/import/template.csv`            GET   CSV 範本
    `/nvrs/import/template.json`           GET   JSON 範本
    `/nvrs/export.csv`                     GET   匯出 CSV
    `/nvrs/export.json`                    GET   匯出 JSON

Stage A stub。
"""
from __future__ import annotations

from flask import Blueprint

nvrs_bp = Blueprint("nvrs", __name__, url_prefix="/nvrs")
