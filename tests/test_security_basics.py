"""
tests/test_security_basics.py
=============================
Day-0 資安修補 #5：5 項 auth pytest。

對應報告 §5.1 動作 1-3 的防護：
1. SECRET_KEY 必須從 env var 注入，無 fallback
2. HOST 預設 127.0.0.1（不暴露公網）
3. CSV/JSON import template 不含明碼密碼
4. 路由可正確讀到 NVR_WEB_SECRET_KEY（整合測試）
5. web/app.py 沒有寫死的 SECRET_KEY fallback（防 regression）

執行：pytest tests/test_security_basics.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# === 動作 1：SECRET_KEY 強制從 env var 注入 ===


def test_create_app_raises_without_secret_key_env(monkeypatch, tmp_path):
    """沒設 NVR_WEB_SECRET_KEY → create_app() 應 raise RuntimeError。"""
    monkeypatch.delenv("NVR_WEB_SECRET_KEY", raising=False)
    from web.app import create_app

    with pytest.raises(RuntimeError, match="NVR_WEB_SECRET_KEY"):
        create_app(db_path=str(tmp_path / "no.db"))


def test_create_app_accepts_injected_secret_key(monkeypatch, tmp_path):
    """測試可注入 secret_key 參數（向後相容既有 132 項測試）。"""
    monkeypatch.delenv("NVR_WEB_SECRET_KEY", raising=False)
    from web.app import create_app

    app = create_app(db_path=str(tmp_path / "ok.db"), secret_key="test-secret")
    assert app.config["SECRET_KEY"] == "test-secret"


def test_create_app_reads_secret_key_from_env(monkeypatch, tmp_path):
    """設 NVR_WEB_SECRET_KEY env var → create_app() 自動讀取。"""
    monkeypatch.setenv("NVR_WEB_SECRET_KEY", "env-secret-abc")
    from web.app import create_app

    app = create_app(db_path=str(tmp_path / "env.db"))
    assert app.config["SECRET_KEY"] == "env-secret-abc"


# === 動作 2：HOST 預設 127.0.0.1 ===


def test_run_web_sh_default_is_localhost():
    """run_web.sh 預設 HOST 應為 127.0.0.1（Day-0 修補 #2）。"""
    here = Path(__file__).resolve().parent.parent  # dashboard/
    sh = (here / "run_web.sh").read_text(encoding="utf-8")
    m = re.search(r'HOST="\$\{NVR_WEB_HOST:-([^}]+)\}"', sh)
    assert m, "找不到 HOST default"
    assert (
        m.group(1).strip() == "127.0.0.1"
    ), f"預設應為 127.0.0.1（防公網意外暴露），got {m.group(1).strip()!r}"


def test_run_web_ps1_default_is_localhost():
    """run_web.ps1 預設 HOST 應為 127.0.0.1。"""
    here = Path(__file__).resolve().parent.parent
    ps1 = (here / "run_web.ps1").read_text(encoding="utf-8")
    m = re.search(r'\$WebHost\s*=\s*if.*?else\s*\{\s*"([^"]+)"', ps1)
    assert m, "找不到 $WebHost default"
    assert m.group(1) == "127.0.0.1", f"預設應為 127.0.0.1，got {m.group(1)!r}"


def test_run_web_bat_default_is_localhost():
    """run_web.bat 預設 HOST 應為 127.0.0.1。"""
    here = Path(__file__).resolve().parent.parent
    bat = (here / "run_web.bat").read_text(encoding="utf-8")
    assert 'set "NVR_WEB_HOST=127.0.0.1"' in bat, "run_web.bat 預設 host 應為 127.0.0.1"


# === 動作 3：CSV/JSON template 不含明碼密碼 ===


def test_csv_template_no_plaintext_password():
    """CSV import template 不應包含明碼密碼（SECRET 這種 placeholder 也不准）。

    Week 6 #017：example_csv 從 web/app.py 搬到 web/blueprints/nvrs_bp.py。
    """
    here = Path(__file__).resolve().parent.parent
    nvrs_bp_py = (here / "web" / "blueprints" / "nvrs_bp.py").read_text(encoding="utf-8")
    m = re.search(r"example_csv\s*=\s*\((.*?)\)", nvrs_bp_py, re.DOTALL)
    assert m, "找不到 example_csv block"
    csv_block = m.group(1)
    # 不准出現 SECRET（明碼密碼 placeholder）或任何看起來像真密碼的字串
    assert "SECRET" not in csv_block, (
        "CSV template 含明碼密碼 placeholder 'SECRET'，"
        "Day-0 修補要求改為 <CHANGE_ME>"
    )
    # 應有 <CHANGE_ME> 佔位符
    assert "<CHANGE_ME>" in csv_block, "CSV template 應使用 <CHANGE_ME> 佔位符"


def test_json_template_no_plaintext_password():
    """JSON import template 不應包含明碼密碼。

    Week 6 #017：example 從 web/app.py 搬到 web/blueprints/nvrs_bp.py。
    """
    here = Path(__file__).resolve().parent.parent
    nvrs_bp_py = (here / "web" / "blueprints" / "nvrs_bp.py").read_text(encoding="utf-8")
    # 抓 password: "..." 的所有出現
    passwords = re.findall(r'"password":\s*"([^"]+)"', nvrs_bp_py)
    assert passwords, "找不到任何 password 欄位"
    for pwd in passwords:
        assert pwd != "SECRET", "JSON template 含明碼密碼 placeholder 'SECRET'"
        assert (
            pwd == "<CHANGE_ME>"
        ), f"JSON template password 應為 '<CHANGE_ME>'，got {pwd!r}"


# === 防 regression：掃描 web/app.py 內的 SECRET_KEY fallback ===


def test_no_hardcoded_secret_key_fallback():
    """web/app.py 不應有寫死的 SECRET_KEY fallback 字串。"""
    here = Path(__file__).resolve().parent.parent
    app_py = (here / "web" / "app.py").read_text(encoding="utf-8")
    # 任何包含 dev-key 字樣的 fallback 都視為 regression
    forbidden_patterns = [
        r"nvr-scanner-dev-key",  # 舊 fallback 字串
        r"change-in-prod",  # 舊 placeholder
        r"dev-secret",  # 其他變形
    ]
    for pat in forbidden_patterns:
        assert not re.search(pat, app_py, re.IGNORECASE), (
            f"web/app.py 含寫死的 SECRET_KEY fallback（pattern: {pat}）。"
            "Day-0 修補要求強制走 env var。"
        )
