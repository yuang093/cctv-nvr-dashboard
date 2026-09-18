"""防呆：dashboard.html 的 staleness 計算不應硬把 "Z" 拼到 ISO 字串。

Bug：recording_latest_at 已是 'YYYY-MM-DDTHH:MM:SSZ'，
template 寫 'new Date(latest + "Z").getTime()' → '...ZZ' → NaN。
"""

from pathlib import Path


DASH_HTML = (
    Path(__file__).resolve().parent.parent / "web" / "templates" / "dashboard.html"
)


def test_dashboard_does_not_concat_z_to_iso_string():
    """確保沒有 'latest + "Z"' 或類似重複加 Z 的 pattern。"""
    content = DASH_HTML.read_text(encoding="utf-8")
    forbidden = ['latest + "Z"', "latest + 'Z'", 'latest+ "Z"']
    for pat in forbidden:
        assert (
            pat not in content
        ), f"dashboard.html 含 {pat!r} — 會把已帶 Z 的 ISO 字串變 ...ZZ → NaN"


def test_dashboard_uses_safe_iso_parse():
    """確保 dashboard 用 'new Date(latest).getTime()' 而非 'latest + Z'。"""
    content = DASH_HTML.read_text(encoding="utf-8")
    # 期待有安全寫法
    assert "new Date(latest)" in content, "應有 new Date(latest).getTime() 安全寫法"


def test_dashboard_handles_nan_gracefully():
    """若 ISO 解析失敗，UI 應顯示 '—' 而非 'NaN 天前'。"""
    content = DASH_HTML.read_text(encoding="utf-8")
    assert "isNaN(lTime)" in content, "應有 isNaN check 避免 NaN 滲到 UI"
    assert "最後更新：—" in content, "fallback 應顯示 '—' 而非 'NaN'"
