"""Week 6 #018 — pages_bp。

URL: `/`, `/clips`, `/dark/toggle`
純頁面 alias（前端用）或 dark mode switch。
"""
from __future__ import annotations

from flask import Blueprint

pages_bp = Blueprint("clips_pages", __name__)
