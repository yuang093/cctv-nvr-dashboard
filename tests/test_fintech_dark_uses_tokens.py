"""驗證 fintech-dark.css 內大部分 hardcode 顏色被替換為 var()。"""

import re
from pathlib import Path


FINTECH_DARK = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "themes"
    / "fintech-dark.css"
)


def test_fintech_dark_uses_var_for_bg():
    """關鍵色票應用 var()。"""
    content = FINTECH_DARK.read_text(encoding="utf-8")
    for token in [
        "--bg-primary",
        "--bg-card",
        "--bg-secondary",
        "--border-primary",
        "--text-primary",
    ]:
        assert f"var({token})" in content, f"fintech-dark.css 應使用 var({token})"


def test_fintech_dark_hardcode_count_reduced():
    """hardcode 顏色數量應減少（從 90+ 降到 30 以下）。"""
    content = FINTECH_DARK.read_text(encoding="utf-8")
    rgba_removed = re.sub(r"rgba\([^)]+\)", "", content)
    var_removed = re.sub(r"var\(--[\w-]+\)", "", rgba_removed)
    hex_matches = re.findall(r"#[0-9a-fA-F]{6}", var_removed)
    assert (
        len(hex_matches) <= 30
    ), f"fintech-dark.css hardcode 顏色應 ≤ 30，實際 {len(hex_matches)} 個：{hex_matches[:10]}"
