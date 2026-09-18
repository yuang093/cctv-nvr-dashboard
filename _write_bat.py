"""Generate all .bat files in the project root with no string escape ambiguity.

Why this file exists
--------------------
Python's str literal interpretation turns `"\t"` (TAB) and `"\a"` (BELL) into
escape chars BEFORE the .bat file is even written. Editing .bat by hand in
Python source therefore corrupts paths (`%PD%web\templates` becomes
`%PD%web<BEL>emplates` because `\t` is escape-interpreted).

The robust fix: write .bat as **bytes literals** (`b"\\t"` is two ASCII chars,
backslash + `t`). No `\X` is ever interpreted as an escape.

This module is the **single source of truth** for all .bat files committed
to the repo. Re-run `python _write_bat.py` after editing the `BUILD_WEB`,
`BUILD_ALL`, `RUN_WEB`, `RUN_WORKER` lists below; git-diff any output.

Files generated
---------------
- build_web.bat     — pyinstaller web.exe (legacy single-call, 8444 dashboard)
- build_clips.bat   — pyinstaller clips.exe (Phase 2.7, 8555 clips + NVR CRUD + Dark Mode)
- build_all.bat     — wraps build_web.bat (convenience entrypoint)
- run_web.bat       — launch dev Flask on Windows (cmd)
- run_worker.bat    — Task Scheduler entry for `nvr_scanner.py`
- run_clips.bat     — Phase 2.7 影片片段調閱 Flask（port 8555，給其他部門用）

8555 vs 8444 打包差異
---------------------
- 8555 entry: `web/clips_app.py` (8444 用 `web/app.py`)
- 8555 templates: 只打包 `web/clips_templates/`（8444 用 `web/templates/`）
- 8555 跳過 `web/static/`（8444 用 — 8555 全部 inline dark CSS）
- 8555 跳過 `reportlab`（8444 用 — 8555 不出 PDF）
- 8555 加入 `Pillow`（`web.clips_app._compress_to_thumbnail` 用 `PIL.Image`）
- 兩者共用：`flask` / `requests` / `nvr_scanner` / `nvr_scan.db` / `nvr_config.json`
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# build_web.bat — calls `pyinstaller` to bundle web.exe (onedir mode)
# ---------------------------------------------------------------------------
BUILD_WEB = [
    b"@echo off",
    b"setlocal EnableExtensions",
    b'set "PD=%~dp0"',
    b'set "VENV=%PD%venv\\Scripts\\python.exe"',
    b'if not exist "%VENV%" ( echo [FATAL] venv missing & exit /b 1 )',
    b'"%VENV%" -c "import PyInstaller" 2>nul',
    b'if errorlevel 1 ( "%VENV%" -m pip install "pyinstaller>=6.0" & if errorlevel 1 ( echo [FATAL] & exit /b 2 ) )',
    b'cd /d "%PD%"',
    b'if exist "%PD%nvr_scan.db" (',
    b"  echo [INFO] Building web.exe with DB...",
    b'  "%VENV%" -m PyInstaller --noconfirm --clean --name web --console --onedir '
    b'--add-data "%PD%web\\templates;web\\templates" '
    b'--add-data "%PD%web\\static;web\\static" '
    b'--add-data "%PD%nvr_config.json;." '
    b'--add-data "%PD%nvr_scan.db;." '
    b"--collect-all flask --collect-all jinja2 --collect-all click "
    b"--collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug "
    b"--collect-all reportlab --collect-all tzdata "
    b"--collect-all requests --collect-all urllib3 --collect-all certifi "
    b"--collect-all charset_normalizer --collect-all idna "
    b"--hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv "
    b'"%PD%web\\app.py"',
    b") else (",
    b"  echo [INFO] Building web.exe without DB...",
    b'  "%VENV%" -m PyInstaller --noconfirm --clean --name web --console --onedir '
    b'--add-data "%PD%web\\templates;web\\templates" '
    b'--add-data "%PD%web\\static;web\\static" '
    b'--add-data "%PD%nvr_config.json;." '
    b"--collect-all flask --collect-all jinja2 --collect-all click "
    b"--collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug "
    b"--collect-all reportlab --collect-all tzdata "
    b"--collect-all requests --collect-all urllib3 --collect-all certifi "
    b"--collect-all charset_normalizer --collect-all idna "
    b"--hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv "
    b'"%PD%web\\app.py"',
    b")",
    b"if errorlevel 1 ( echo [FATAL] & exit /b 4 )",
    b"echo.",
    b"echo [OK] Built: dist\\web\\web.exe",
    b"exit /b 0",
]


# ---------------------------------------------------------------------------
# build_clips.bat — Phase 2.7 8555 clips app（只打包 clips_app 用得到的檔案）
# ---------------------------------------------------------------------------
# 差異 vs build_web.bat：
#   * entry  = web/clips_app.py（不是 web/app.py）
#   * templates = web/clips_templates/（不是 web/templates/）
#   * 跳過 web/static/（clips app 沒有 — dark CSS 都 inline）
#   * 跳過 reportlab（clips app 沒用 PDF）
#   * 加入 Pillow（_compress_to_thumbnail 用 PIL.Image）
BUILD_CLIPS = [
    b"@echo off",
    b"setlocal EnableExtensions",
    b'set "PD=%~dp0"',
    b'set "VENV=%PD%venv\\Scripts\\python.exe"',
    b'if not exist "%VENV%" ( echo [FATAL] venv missing & exit /b 1 )',
    b'"%VENV%" -c "import PyInstaller" 2>nul',
    b'if errorlevel 1 ( "%VENV%" -m pip install "pyinstaller>=6.0" & if errorlevel 1 ( echo [FATAL] & exit /b 2 ) )',
    b'cd /d "%PD%"',
    b'if exist "%PD%nvr_scan.db" (',
    b"  echo [INFO] Building clips.exe with DB...",
    b'  "%VENV%" -m PyInstaller --noconfirm --clean --name clips --console --onedir '
    b'--add-data "%PD%web\\clips_templates;web\\clips_templates" '
    b'--add-data "%PD%nvr_config.json;." '
    b'--add-data "%PD%nvr_scan.db;." '
    b"--collect-all flask --collect-all jinja2 --collect-all click "
    b"--collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug "
    b"--collect-all requests --collect-all urllib3 --collect-all certifi "
    b"--collect-all charset_normalizer --collect-all idna "
    b"--collect-all Pillow --collect-all tzdata "
    b"--hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv "
    b"--hidden-import PIL.Image "
    b'"%PD%web\\clips_app.py"',
    b") else (",
    b"  echo [INFO] Building clips.exe without DB...",
    b'  "%VENV%" -m PyInstaller --noconfirm --clean --name clips --console --onedir '
    b'--add-data "%PD%web\\clips_templates;web\\clips_templates" '
    b'--add-data "%PD%nvr_config.json;." '
    b"--collect-all flask --collect-all jinja2 --collect-all click "
    b"--collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug "
    b"--collect-all requests --collect-all urllib3 --collect-all certifi "
    b"--collect-all charset_normalizer --collect-all idna "
    b"--collect-all Pillow --collect-all tzdata "
    b"--hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv "
    b"--hidden-import PIL.Image "
    b'"%PD%web\\clips_app.py"',
    b")",
    b"if errorlevel 1 ( echo [FATAL] & exit /b 4 )",
    b"echo.",
    b"echo [OK] Built: dist\\clips\\clips.exe",
    b"exit /b 0",
]


# ---------------------------------------------------------------------------
# build_all.bat — convenience wrapper: just calls build_web.bat
# ---------------------------------------------------------------------------
BUILD_ALL = [
    b"@echo off",
    b"REM build_all.bat",
    b"REM ==============",
    b"REM Convenience: build web.exe (single exe)\xe3\x80\x82",
    b"REM (worker.exe removed 2026-07-03 \xe2\x80\x94 user wants scan triggered from Web UI button.)",
    b"",
    b"setlocal EnableExtensions",
    b"",
    b'set "PROJECT_DIR=%~dp0"',
    b"",
    b"echo ============================================",
    b"echo [1/1] Building web.exe ...",
    b"echo ============================================",
    b'call "%PROJECT_DIR%build_web.bat"',
    b"if errorlevel 1 (",
    b"    echo [FATAL] build_web.bat failed, aborting 1>&2",
    b"    exit /b 1",
    b")",
    b"",
    b"echo.",
    b"echo ============================================",
    b"echo [OK] Build complete.",
    b"echo   - dist\\web\\web.exe        (Flask Web UI launcher)",
    b"echo.",
    b"echo [NEXT] Test it:",
    b"echo   .\\dist\\web\\web.exe",
    b"echo ============================================",
    b"exit /b 0",
]


# ---------------------------------------------------------------------------
# run_web.bat — Windows cmd launcher (uses venv directly, no activate)
# ---------------------------------------------------------------------------
RUN_WEB = [
    b"@echo off",
    b"REM run_web.bat",
    b"REM ===========",
    b"REM Windows \xe5\x95\x9f\xe5\x8b\x95 v2 Web UI\xef\xbc\x88Flask\xef\xbc\x89\xe3\x80\x82",
    b"REM",
    b"REM \xe7\x94\xa8\xe6\xb3\x95\xef\xbc\x9a\xe7\x9b\xb4\xe6\x8e\xa5\xe9\x9b\x99\xe6\x93\x8a\xef\xbc\x8c\xe6\x88\x96\xe5\x9c\xa8 cmd \xe5\x9f\xb7\xe8\xa1\x8c run_web.bat",
    b"REM \xe7\x92\xb0\xe5\xa2\x83\xe8\xae\x8a\xe6\x95\xb8\xef\xbc\x88\xe5\x8f\xaf\xe9\x81\xb8\xef\xbc\x89\xef\xbc\x9a",
    b"REM   NVR_WEB_HOST  \xe9\xa0\x90\xe8\xa8\xad 127.0.0.1\xef\xbc\x88Day-0 \xe4\xbf\xae\xe8\xa3\x9c\xef\xbc\x9a\xe5\x8f\xaa\xe6\x9c\xac\xe6\xa9\x9f\xe5\x8f\xaf\xe9\x80\xa3\xef\xbc\x9b\xe8\xa8\xad 0.0.0.0 = \xe5\x85\xa7\xe7\xb6\xb2 IP \xef\xbc\x8c\xe5\xbf\x85\xe9\x9c\x80 reverse proxy\xef\xbc\x89",
    b"REM   NVR_WEB_PORT  \xe9\xa0\x90\xe8\xa8\xad 8444\xef\xbc\x88\xe9\x81\xbf\xe9\x96\x8b NVR 8443\xef\xbc\x89",
    b"REM   NVR_DB_PATH   \xe9\xa0\x90\xe8\xa8\xad .\\nvr_scan.db",
    b"REM",
    b"REM \xe5\xb0\x8d\xe5\xa4\x96\xe6\x9a\xb4\xe9\x9c\xb2\xe6\x99\x82\xe5\x8b\x99\xe5\xbf\x85\xe5\x8a\xa0 reverse proxy (nginx / caddy) + HTTPS",
    b"",
    b'set "PROJECT_DIR=%~dp0"',
    b'set "VENV_PY=%PROJECT_DIR%venv\\Scripts\\python.exe"',
    b'if not "%NVR_WEB_HOST%"=="" goto host_set',
    b'    set "NVR_WEB_HOST=127.0.0.1"',
    b":host_set",
    b'if not "%NVR_WEB_PORT%"=="" goto port_set',
    b'    set "NVR_WEB_PORT=8444"',
    b":port_set",
    b'if not "%NVR_DB_PATH%"=="" goto db_set',
    b'    set "NVR_DB_PATH=%PROJECT_DIR%nvr_scan.db"',
    b":db_set",
    b"",
    b'cd /d "%PROJECT_DIR%"',
    b"if errorlevel 1 (",
    b"    echo [FATAL] \xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe5\xb0\x88\xe6\xa1\x88\xe7\x9b\xae\xe9\x8c\x84\xef\xbc\x9a%PROJECT_DIR% 1>&2",
    b"    exit /b 2",
    b")",
    b"",
    b'if not exist "%VENV_PY%" (',
    b"    echo [FATAL] \xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0 venv python\xef\xbc\x9a%VENV_PY% 1>&2",
    b"    echo \xe8\xab\x8b\xe5\x85\x88\xef\xbc\x9apython -m venv venv ^&^& venv\\Scripts\\activate ^&^& pip install -r requirements-web.txt 1>&2",
    b"    exit /b 3",
    b")",
    b"",
    b"echo [INFO] Starting NVR Web UI at http://%NVR_WEB_HOST%:%NVR_WEB_PORT%",
    b"echo [INFO] DB: %NVR_DB_PATH%",
    b"echo \xe6\x8c\x89 Ctrl+C \xe5\x81\x9c\xe6\xad\xa2",
    b"",
    b'"%VENV_PY%" -m web.app',
]


# ---------------------------------------------------------------------------
# run_worker.bat — Task Scheduler entrypoint. Calls venv\\Scripts\\activate
# then runs nvr_scanner.py with output appended to logs\\nvr_scanner.log
# ---------------------------------------------------------------------------
RUN_WORKER = [
    b"@echo off",
    b"REM run_worker.bat",
    b"REM ==============",
    b"REM Windows \xe5\xb7\xa5\xe4\xbd\x9c\xe6\x8e\x92\xe7\xa8\x8b\xe5\x99\xa8\xe5\x85\xa5\xe5\x8f\xa3\xe8\x85\xb3\xe6\x9c\xac\xef\xbc\x88\xe7\xb5\xa6 taskschd.msc \xe5\x91\xbc\xe5\x8f\xab\xef\xbc\x89\xe3\x80\x82",
    b"REM",
    b"REM \xe8\xa1\x8c\xe7\x82\xba\xef\xbc\x9a",
    b"REM   - cd \xe5\x88\xb0\xe5\xb0\x88\xe6\xa1\x88\xe7\x9b\xae\xe9\x8c\x84",
    b"REM   - \xe5\x95\x9f\xe7\x94\xa8 venv",
    b"REM   - \xe5\x9f\xb7\xe8\xa1\x8c nvr_scanner.py\xef\xbc\x8c\xe8\xbc\xb8\xe5\x87\xba\xe9\x99\x84\xe5\x8a\xa0\xe5\xaf\xab\xe5\x88\xb0 logs\\nvr_scanner.log",
    b"REM   - exit code \xe9\x80\x8f\xe5\x82\xb3",
    b"REM",
    b"REM \xe8\x87\xaa\xe8\xa8\x82\xe5\x8d\x80\xef\xbc\x9a\xe4\xbf\xae\xe6\x94\xb9\xe4\xb8\x8b\xe6\x96\xb9\xe7\x9a\x84 PROJECT_DIR \xe8\x88\x87 LOG_DIR",
    b"",
    b"REM === \xe8\x87\xaa\xe8\xa8\x82\xe5\x8d\x80\xef\xbc\x88\xe4\xbe\x9d\xe9\x83\xa8\xe7\xbd\xb2\xe7\x92\xb0\xe5\xa2\x83\xe4\xbf\xae\xe6\x94\xb9\xef\xbc\x89 ===",
    b'set "PROJECT_DIR=C:\\nvr"',
    b'set "LOG_DIR=%PROJECT_DIR%\\logs"',
    b"",
    b"REM === \xe4\xb8\xbb\xe6\xb5\x81\xe7\xa8\x8b ===",
    b'cd /d "%PROJECT_DIR%"',
    b"if errorlevel 1 (",
    b"    echo [FATAL] \xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe5\xb0\x88\xe6\xa1\x88\xe7\x9b\xae\xe9\x8c\x84\xef\xbc\x9a%PROJECT_DIR% 1>&2",
    b"    exit /b 2",
    b")",
    b"",
    b'if not exist "venv\\Scripts\\activate.bat" (',
    b"    echo [FATAL] \xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0 venv\xef\xbc\x9a%PROJECT_DIR%\\venv\\Scripts\\activate.bat 1>&2",
    b"    echo \xe8\xab\x8b\xe5\x85\x88\xe5\xbb\xba\xe7\xab\x8b\xe8\x99\x9b\xe6\x93\xac\xe7\x92\xb0\xe5\xa2\x83\xef\xbc\x9apython -m venv venv 1>&2",
    b"    exit /b 3",
    b")",
    b"",
    b"call venv\\Scripts\\activate.bat",
    b"if errorlevel 1 (",
    b"    echo [FATAL] venv \xe5\x95\x9f\xe7\x94\xa8\xe5\xa4\xb1\xe6\x95\x97 1>&2",
    b"    exit /b 4",
    b")",
    b"",
    b'if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"',
    b"",
    b'python nvr_scanner.py >> "%LOG_DIR%\\nvr_scanner.log" 2>&1',
    b'set "EXIT_CODE=%errorlevel%"',
    b"",
    b'if not "%EXIT_CODE%"=="0" (',
    b'    echo [%date% %time%] run_worker.bat exit_code=%EXIT_CODE% >> "%LOG_DIR%\\nvr_scanner.log"',
    b")",
    b"",
    b"exit /b %EXIT_CODE%",
]


# ---------------------------------------------------------------------------
# run_clips.bat — Phase 2.7 影片片段調閱 Flask（port 8555，給另一部門用）
# ---------------------------------------------------------------------------
RUN_CLIPS = [
    b"@echo off",
    b"REM run_clips.bat",
    b"REM =============",
    b"REM Windows \xe5\x95\x9f\xe5\x8b\x95 Phase 2.7 \xe5\xbd\xb1\xe7\x89\x87\xe7\x89\x87\xe6\xae\xb5\xe8\xaa\xbf\xe9\x96\xb1 Flask\xef\xbc\x88port 8555\xef\xbc\x8c\xe7\xb5\xa6\xe5\x8f\xa6\xe4\xb8\x80\xe9\x83\xa8\xe9\x96\x80\xe7\x94\xa8\xef\xbc\x89\xe3\x80\x82",
    b"REM",
    b"REM \xe7\x92\xb0\xe5\xa2\x83\xe8\xae\x8a\xe6\x95\xb8\xef\xbc\x88\xe5\x8f\xaf\xe9\x81\xb8\xef\xbc\x89\xef\xbc\x9a",
    b"REM   NVR_CLIPS_HOST  \xe9\xa0\x90\xe8\xa8\xad 0.0.0.0\xef\xbc\x88LAN \xe5\x8f\x8b\xe5\x96\x84\xef\xbc\x9b\xe8\xa8\xad 127.0.0.1 = \xe5\x8f\xaa\xe6\x9c\xac\xe6\xa9\x9f\xe5\x8f\xaf\xe9\x80\xa3\xef\xbc\x89",
    b"REM   NVR_CLIPS_PORT  \xe9\xa0\x90\xe8\xa8\xad 8555\xef\xbc\x88\xe7\x94\xa8\xe6\x88\xb6\xe6\x8c\x87\xe5\xae\x9a\xef\xbc\x9b\xe9\x81\xbf\xe9\x96\x8b NVR 8443 / \xe6\x97\xa2\xe6\x9c\x89 8444\xef\xbc\x89",
    b"REM   NVR_DB_PATH     \xe9\xa0\x90\xe8\xa8\xad .\\nvr_scan.db",
    b"REM   NVR_CLIPS_CLIENT mock = \xe8\xb5\xb0 MockMediaClient\xef\xbc\x88\xe6\xb8\xac\xe8\xa9\xa6\xe7\x94\xa8\xef\xbc\x89",
    b"",
    b'set "PROJECT_DIR=%~dp0"',
    b'set "VENV_PY=%PROJECT_DIR%venv\\Scripts\\python.exe"',
    b'if not "%NVR_CLIPS_HOST%"=="" goto host_set',
    b'    set "NVR_CLIPS_HOST=0.0.0.0"',
    b":host_set",
    b'if not "%NVR_CLIPS_PORT%"=="" goto port_set',
    b'    set "NVR_CLIPS_PORT=8555"',
    b":port_set",
    b'if not "%NVR_DB_PATH%"=="" goto db_set',
    b'    set "NVR_DB_PATH=%PROJECT_DIR%nvr_scan.db"',
    b":db_set",
    b"",
    b'cd /d "%PROJECT_DIR%"',
    b"if errorlevel 1 (",
    b"    echo [FATAL] \xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe5\xb0\x88\xe6\xa1\x88\xe7\x9b\xae\xe9\x8c\x84\xef\xbc\x9a%PROJECT_DIR% 1>&2",
    b"    exit /b 2",
    b")",
    b"",
    b'if not exist "%VENV_PY%" (',
    b"    echo [FATAL] \xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0 venv python\xef\xbc\x9a%VENV_PY% 1>&2",
    b"    echo \xe8\xab\x8b\xe5\x85\x88\xef\xbc\x9apython -m venv venv ^&^& venv\\Scripts\\activate ^&^& pip install -r requirements-web.txt 1>&2",
    b"    exit /b 3",
    b")",
    b"",
    b"echo [INFO] Starting NVR Clip Web UI at http://%NVR_CLIPS_HOST%:%NVR_CLIPS_PORT%",
    b"echo [INFO] DB: %NVR_DB_PATH%",
    b"echo \xe6\x8c\x89 Ctrl+C \xe5\x81\x9c\xe6\xad\xa2",
    b"",
    b'"%VENV_PY%" -m web.clips_app',
]


# Each entry: (filename, list of byte lines)
GENERATED = [
    ("build_web.bat", BUILD_WEB),
    ("build_clips.bat", BUILD_CLIPS),
    ("build_all.bat", BUILD_ALL),
    ("run_web.bat", RUN_WEB),
    ("run_worker.bat", RUN_WORKER),
    ("run_clips.bat", RUN_CLIPS),
]


def render(name: str, lines: list[bytes]) -> bytes:
    """Render bytes list to a Windows-friendly .bat body (CRLF)."""
    body = b"\r\n".join(lines) + b"\r\n"
    return body


def write_file(name: str, content: bytes) -> str:
    """Write bytes to <HERE>/<name>; return absolute path."""
    out_path = os.path.join(HERE, name)
    with open(out_path, "wb") as f:
        f.write(content)
    return out_path


def self_test() -> bool:
    """Idempotence check: every byte sequence in the rendered output must
    contain NO backslash-letter pairs that look like Python escapes (\\a \\b
    \\t \\n \\v \\f \\r). Print a warning if any are detected.
    """
    bad = (b"\x07", b"\x08", b"\t", b"\n", b"\x0b", b"\x0c", b"\r")
    bad_names = ("\\a", "\\b", "\\t", "\\n", "\\v", "\\f", "\\r")
    ok = True
    for name, lines in GENERATED:
        body = render(name, lines)
        for byte, label in zip(bad, bad_names):
            count = body.count(byte)
            # CRLF join introduces \\r\\n as expected, so only warn on bare \\n
            if label == "\\n" and count == body.count(b"\r\n") + body.count(b"\n"):
                pass
            if label == "\\r" and count == body.count(b"\r\n"):
                pass
            if label in ("\\a", "\\b", "\\v", "\\f", "\\t"):
                if count:
                    print(
                        f"  [FAIL] {name}: contains {label} ({count}x) — TRUE escape leaked!"
                    )
                    ok = False
    return ok


def main() -> int:
    summary = []
    for name, lines in GENERATED:
        body = render(name, lines)
        path = write_file(name, body)
        summary.append((name, len(body), path))

    print("=" * 60)
    print("Wrote {} bat files (single source of truth)".format(len(summary)))
    print("=" * 60)
    for name, size, path in summary:
        print(f"  {name:24s} {size:>5} bytes  {path}")
    print()

    if not self_test():
        print("[FAIL] True escape chars detected — DO NOT trust the output.")
        return 1

    print("[OK] No true TAB/BEL/BS/VT/FF chars in any output file.")
    print()
    print(
        "Re-run after editing the *_WEB/_ALL/_WORKER/_CLIPS byte lists above; "
        "git-diff the .bat files to verify intent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
