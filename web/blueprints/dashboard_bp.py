"""Week 6 #017 — dashboard_bp。

URL prefix: `/`
路由（5 條）：
    `/`                   GET   首頁總覽（dashboard.html）
    `/theme`              GET   主題預覽
    `/theme/apply`        POST  套用主題
    `/fleet`              GET   跨 NVR 概覽
    `/dark/toggle`        POST  深色模式切換

背景關聯：GET / 內做「即時線上 cam 計數」（呼叫 _count_online_cameras）。
Week 6 Stage A — 本檔案此時為 stub；Stage A 期間將從 web/app.py 內 `_register_routes()`
完整複製原本實作，最後 Stage B 才從 app.py 刪除舊版。
"""
from __future__ import annotations

from flask import Blueprint

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/")

# Stage A 後將從 web/app.py 搬入；目前 stub 不註冊任何 route（factory
# 雙 register 期間由原 app.py 提供路由）。
