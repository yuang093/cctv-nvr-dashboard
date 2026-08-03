"""
tests/test_host_bind.py
=======================
驗證 web.app.main() 的 host bind 邏輯：
  - 預設綁 0.0.0.0（LAN-friendly）
  - NVR_WEB_HOST=127.0.0.1 只本機
  - NVR_WEB_HOST=192.168.x.x 綁特定 LAN IP
  - 環境變數決定 port

策略：monkeypatch 掉 socket.socket.bind，記錄被呼叫時的 host/port，
驗證呼叫過且 host/port 正確。不真正 bind（避免環境 port 衝突）。
"""
from __future__ import annotations

import socket
import re


def _capture_bind(monkeypatch, captured):
    """把 socket.socket.bind 換成記錄用版本。"""
    real_init = socket.socket.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "__init__", patched_init)

    def patched_bind(self, addr):
        captured.append((addr[0], addr[1]))

    monkeypatch.setattr(socket.socket, "bind", patched_bind)
    monkeypatch.setattr(socket.socket, "listen", lambda *a, **kw: None)


def _make_fake_app(db_path, captured_bind, debug=False):
    """建一個只在 bind 階段記錄的假 app。"""

    class FakeApp:
        config = {"DB_PATH": db_path}

        def run(self, host, port, **kwargs):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((host, port))

    return FakeApp()


def test_default_binds_0000(monkeypatch, tmp_path):
    """不設 NVR_WEB_HOST → 0.0.0.0:8444。"""
    monkeypatch.delenv("NVR_WEB_HOST", raising=False)
    db = str(tmp_path / "bind.db")
    monkeypatch.setenv("NVR_DB_PATH", db)
    import web.app as webapp
    captured = []
    _capture_bind(monkeypatch, captured)
    webapp.app = _make_fake_app(db, captured)
    webapp.main()
    assert captured == [("0.0.0.0", 8444)], f"預設 0.0.0.0:8444, got {captured}"


def test_env_127_keeps_localhost(monkeypatch, tmp_path):
    """NVR_WEB_HOST=127.0.0.1 → 只本機。"""
    monkeypatch.setenv("NVR_WEB_HOST", "127.0.0.1")
    db = str(tmp_path / "l.db")
    monkeypatch.setenv("NVR_DB_PATH", db)
    import web.app as webapp
    captured = []
    _capture_bind(monkeypatch, captured)
    webapp.app = _make_fake_app(db, captured)
    webapp.main()
    assert captured == [("127.0.0.1", 8444)]


def test_env_lan_ip(monkeypatch, tmp_path):
    """NVR_WEB_HOST=192.168.133.89 → 綁特定 LAN IP。"""
    monkeypatch.setenv("NVR_WEB_HOST", "192.168.133.89")
    db = str(tmp_path / "l2.db")
    monkeypatch.setenv("NVR_DB_PATH", db)
    import web.app as webapp
    captured = []
    _capture_bind(monkeypatch, captured)
    webapp.app = _make_fake_app(db, captured)
    webapp.main()
    assert captured == [("192.168.133.89", 8444)]


def test_env_custom_port(monkeypatch, tmp_path):
    """NVR_WEB_PORT=9000 → 自訂 port。"""
    monkeypatch.setenv("NVR_WEB_PORT", "9000")
    db = str(tmp_path / "p.db")
    monkeypatch.setenv("NVR_DB_PATH", db)
    import web.app as webapp
    captured = []
    _capture_bind(monkeypatch, captured)
    webapp.app = _make_fake_app(db, captured)
    webapp.main()
    assert captured == [("0.0.0.0", 9000)]


def test_run_web_ps1_default_is_0000():
    """run_web.ps1 在環境變數未設時，預設值是 0.0.0.0。"""
    ps1 = open(r"C:\cc\NVR\run_web.ps1", encoding="utf-8").read()
    m = re.search(r'\$WebHost\s*=\s*if.*?else\s*\{\s*"([^"]+)"', ps1)
    assert m, "找不到 $WebHost default"
    assert m.group(1) == "0.0.0.0"


def test_run_web_sh_default_is_0000():
    """run_web.sh 預設也是 0.0.0.0。"""
    sh = open(r"C:\cc\NVR\run_web.sh", encoding="utf-8").read()
    m = re.search(r'HOST="\$\{NVR_WEB_HOST:-([^}]+)\}"', sh)
    assert m, "找不到 HOST default"
    assert m.group(1).strip() == "0.0.0.0"


def test_run_web_bat_default_is_0000():
    """run_web.bat (cmd) 預設也是 0.0.0.0。"""
    bat = open(r"C:\cc\NVR\run_web.bat", encoding="utf-8").read()
    assert 'set "NVR_WEB_HOST=0.0.0.0"' in bat, "預設 host 應為 0.0.0.0"
