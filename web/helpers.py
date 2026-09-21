"""Week 6 共用 helpers（8444 + 8555 跨 bp 共用）。

由 web/app.py 抽出：
- TAIPEI_TZ                Asia/Taipei 時區（datetime 計算用）
- _to_taipei_str          UTC ISO 字串 → Asia/Taipei 字串
- _safe_int               query string → int（無效 fallback 防 500）

Week 6 Stage A：app.py 仍 re-export 這些名稱（from web.helpers import ...）
讓既有測試與第三方程式碼繼續運作；Stage B 後再決定是否收斂 import。
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# === 時區：DB 存 UTC，UI / PDF 顯示為 Asia/Taipei ===
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def to_taipei_str(iso_utc: str | None) -> str:
    """把 ISO 8601 UTC 字串轉成 Asia/Taipei（顯示用）。

    輸入：'2026-07-03T05:39:02Z' 或 '2026-07-03T05:39:02+00:00'
    輸出：'2026-07-03 13:39:02'
    None / 空字串 → 原文回傳。
    """
    if not iso_utc:
        return iso_utc or ""
    try:
        s = iso_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return iso_utc


def safe_int(
    value: str | None, default: int, *, min_val: int = 0, max_val: int = 2**31
) -> int:
    """把 query string 轉 int，無效時退回 default。

    防止 `?page=abc` 噴 ValueError → 500。
    """
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (ValueError, TypeError):
        return default
    if n < min_val or n > max_val:
        return default
    return n
