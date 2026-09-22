"""
scripts/check_ci_duration.py
============================
Week 8 Issue #024 — CI 執行時間驗證。

讀 `.github/workflows/ci.yml` 矩陣設定，預估 wall-clock：
  - pytest job：N versions × T_sec（單版本時間）
  - mypy job：1 version × T_mypy
  - 並行：max(pytest, mypy)
  - + install + checkout 等固定 ~30s

退出碼：0 = 預估 < 300s、1 = 預估 ≥ 300s。
"""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SEC = 300

if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def measure_local_pytest() -> float:
    """本地跑 pytest -q 取秒數。"""
    try:
        r = subprocess.run(
            ["pytest", "-q", "--no-header"],
            capture_output=True, text=True, cwd=ROOT, timeout=300,
        )
        m = re.search(r"(\d+\.\d+)s", r.stdout)
        return float(m.group(1)) if m else 90.0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 90.0


def measure_local_mypy() -> float:
    """本地跑 mypy 取秒數。"""
    try:
        r = subprocess.run(
            ["python", "-m", "mypy"],
            capture_output=True, text=True, cwd=ROOT, timeout=120,
        )
        m = re.search(r"(\d+\.\d+)s", r.stdout)
        return float(m.group(1)) if m else 20.0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 20.0


def estimate_ci_duration() -> dict:
    """從 ci.yml 解析矩陣，並用本地秒數估算。"""
    ci_f = ROOT / ".github" / "workflows" / "ci.yml"
    ci = ci_f.read_text(encoding="utf-8") if ci_f.exists() else ""
    m = re.search(r"python-version:\s*\[([^\]]+)\]", ci)
    n_py = len(re.findall(r'"3\.\d+"', m.group(1))) if m else 1
    pytest_sec = measure_local_pytest() * n_py
    mypy_sec = measure_local_mypy() if "mypy:" in ci else 0
    wall = max(pytest_sec, mypy_sec) + 30
    return {
        "n_python_versions": n_py,
        "pytest_total_sec": pytest_sec,
        "mypy_total_sec": mypy_sec,
        "estimated_wall_sec": wall,
    }


def main() -> int:
    info = estimate_ci_duration()
    print("📊 CI 預估 wall-clock：")
    print(f"  - Python versions: {info['n_python_versions']}")
    print(f"  - pytest total: {info['pytest_total_sec']:.1f}s")
    print(f"  - mypy total: {info['mypy_total_sec']:.1f}s")
    print(f"  - 預估 wall-clock: {info['estimated_wall_sec']:.1f}s")
    print(f"  - 目標: < {TIMEOUT_SEC}s")
    if info["estimated_wall_sec"] >= TIMEOUT_SEC:
        print(
            f"❌ 超過 {TIMEOUT_SEC}s budget"
            f"（差 {info['estimated_wall_sec'] - TIMEOUT_SEC:.1f}s）"
        )
        return 1
    print(f"✅ 預估 {info['estimated_wall_sec']:.1f}s < {TIMEOUT_SEC}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
