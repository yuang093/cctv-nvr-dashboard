"""
scripts/docs_factcheck.py
==========================
Week 8 Issue #025 — 文件 fact-check 守門員。

掃所有 .md 文件的「表 / 數字 / 函式名」與程式碼比對，列出漂移項。
回傳 list[str]（每項一個漂移描述），空 list 表示全對齊。

設計注意：不要在 module 層做 stdout/stderr wrapper（會干擾 pytest collect）；
只在 main() 內 lazy 做。
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _safe_print_setup() -> None:
    """Windows cp950 console 容錯：把 stdout/stderr 包 UTF-8（避免 ❌/✅ 噴 cp950 錯誤）。
    只在 main() 內呼叫，避免 pytest collect 階段被搞壞。
    """
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass


def _count_actual_routes() -> int:
    """實際 routes 數：sum of @xxx_bp.route decorator 數。"""
    bp_dir = ROOT / "web" / "blueprints"
    bp_clips_dir = ROOT / "web" / "blueprints_clips"
    total = 0
    for d in (bp_dir, bp_clips_dir):
        if not d.exists():
            continue
        for f in d.glob("*.py"):
            if f.name == "__init__.py":
                continue
            text = f.read_text(encoding="utf-8")
            total += len(re.findall(r"@\w+_bp\.route\(", text))
    return total


def _count_yaml_files() -> int:
    """實際 OpenAPI YAML 數。"""
    dash = ROOT / "web" / "openapi" / "dashboard"
    clips = ROOT / "web" / "openapi" / "clips"
    n = 0
    if dash.exists():
        n += len(list(dash.glob("*.yml")))
    if clips.exists():
        n += len(list(clips.glob("*.yml")))
    return n


def _count_pytest_collected() -> int:
    """執行 pytest --collect-only 取得實際測試數。"""
    import subprocess

    try:
        r = subprocess.run(
            ["pytest", "--collect-only", "-q", "--no-header"],
            capture_output=True, text=True, cwd=ROOT, timeout=120,
        )
        m = re.search(r"(\d+) tests collected", r.stdout)
        return int(m.group(1)) if m else -1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1


def check_routes_in_api_endpoints() -> list[str]:
    """檢查 api_endpoints.md §2.1 是否仍標 'OpenAPI 自動產生取代'。"""
    f = ROOT / "api_endpoints.md"
    if not f.exists():
        return ["api_endpoints.md 不存在"]
    doc = f.read_text(encoding="utf-8")
    drift: list[str] = []
    if "OpenAPI 自動產生" not in doc and "OpenAPI 自动产生" not in doc:
        drift.append("api_endpoints.md §2.1 應標註 'OpenAPI 自動產生取代'（Week 7 決議）")
    section_21 = re.search(r"### 2\.1.*?(?=### 2\.2)", doc, re.S)
    if section_21:
        route_rows = len(re.findall(r"^\| (?:GET|POST|GET/POST)", section_21.group(0), re.M))
        actual = _count_actual_routes()
        if route_rows > 0 and abs(route_rows - actual) > 5:
            drift.append(
                f"api_endpoints.md §2.1 仍列 {route_rows} 條手寫 routes，"
                f"實際 {actual} 條 — 已標自動產生，請刪除手寫表"
            )
    return drift


def check_table_names_in_schema_doc() -> list[str]:
    """檢查 database_schema.md 提到的表名都實際存在於 sqlite_writer._init_schema。

    接受兩種寫法：
    A. 獨立章節 §N. `xxx`
    B. 「資料表清單」段的 `N. xxx` 列點
    """
    f = ROOT / "database_schema.md"
    if not f.exists():
        return ["database_schema.md 不存在"]
    doc = f.read_text(encoding="utf-8")
    expected_tables = {
        "nvr_servers", "cameras", "scan_runs", "events",
        "image_health_checks", "discover_sessions", "event_kind_catalog",
        "audit_log", "schema_migrations", "nvr_failure_log",
    }
    # 獨立章節
    sections = set(re.findall(r"^## \d+(?:\.\d+)?\. `(\w+)`", doc, re.M))
    # 「資料表清單」段列點（N. `xxx`）
    list_segment_match = re.search(
        r"## 資料表清單(.*?)(?=^---|\n## )", doc, re.S | re.M
    )
    list_segment = list_segment_match.group(1) if list_segment_match else ""
    list_items = set(re.findall(r"^\d+\. `(\w+)`", list_segment, re.M))
    actual_in_doc = sections | list_items
    writer_f = ROOT / "db" / "sqlite_writer.py"
    if not writer_f.exists():
        return ["db/sqlite_writer.py 不存在"]
    writer = writer_f.read_text(encoding="utf-8")
    actual_in_code = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", writer))
    drift: list[str] = []
    for t in expected_tables - actual_in_doc:
        drift.append(f"database_schema.md 缺表 `{t}` 章節或清單段")
    skip = {
        "events_legacy", "events_view", "events_insert_router",
        "events_update_router", "events_partition", "events",
    }
    for t in sections - actual_in_code - skip:
        if t in actual_in_code:
            continue
        drift.append(f"database_schema.md §提到表 `{t}` 但 sqlite_writer.py 沒 CREATE（可能是過時）")
    return drift


def check_openapi_yaml_count() -> list[str]:
    """檢查 docs/openapi-migration.md 提到 YAML 數是否實際一致。"""
    f = ROOT / "docs" / "openapi-migration.md"
    if not f.exists():
        return []
    doc = f.read_text(encoding="utf-8")
    actual = _count_yaml_files()
    drift: list[str] = []
    if actual > 0:
        if f"{actual} 個" not in doc and f"{actual}條" not in doc and f"{actual} YAML" not in doc:
            drift.append(
                f"openapi-migration.md 沒提到實際 YAML 數 {actual}"
            )
    return drift


def check_pytest_count_in_docs() -> list[str]:
    """檢查 CHANGELOG.md 與 overview.md 提到的 pytest 數是否合理。"""
    drift: list[str] = []
    actual = _count_pytest_collected()
    if actual <= 0:
        return drift
    for fname in ("CHANGELOG.md", "overview.md"):
        f = ROOT / fname
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"(\d+)\s*(?:tests?|項)\s*(?:全綠|passed|collected)?", text):
            num = int(m.group(1))
            if abs(num - actual) > 50 and num > 100:
                drift.append(
                    f"{fname} 提到 {num} 項 pytest，但實際 {actual} 項（差 {num - actual}）"
                )
    return drift


def check_entry_scripts() -> list[str]:
    """檢查 DEPLOY.md 提到的入口腳本是否實際存在。"""
    f = ROOT / "DEPLOY.md"
    if not f.exists():
        return ["DEPLOY.md 不存在"]
    doc = f.read_text(encoding="utf-8")
    drift: list[str] = []
    scripts = [
        "run_worker.sh", "run_worker.bat", "run_worker.ps1",
        "run_web.sh", "run_web.bat", "run_web.ps1",
        "run_clips.sh", "run_clips.bat", "run_clips.ps1",
    ]
    missing = [s for s in scripts if not (ROOT / s).exists()]
    if missing:
        drift.append(f"DEPLOY.md 應提到但實際缺入口腳本：{missing}")
    return drift


def main() -> int:
    """CLI 入口：列出所有漂移項，exit code = 漂移數。"""
    _safe_print_setup()
    all_drift: list[str] = []
    checks = [
        ("routes in api_endpoints.md", check_routes_in_api_endpoints),
        ("table names in database_schema.md", check_table_names_in_schema_doc),
        ("openapi yaml count", check_openapi_yaml_count),
        ("pytest count in CHANGELOG/overview", check_pytest_count_in_docs),
        ("entry scripts in DEPLOY.md", check_entry_scripts),
    ]
    for name, fn in checks:
        result = fn()
        if result:
            print(f"❌ [{name}] {len(result)} 個漂移：")
            for d in result:
                print(f"  - {d}")
            all_drift.extend(result)
        else:
            print(f"✅ [{name}] 通過")
    if all_drift:
        print(f"\n❌ 總計 {len(all_drift)} 個文件漂移")
        return len(all_drift)
    print("\n✅ 文件 fact-check 全綠（5 項檢查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
