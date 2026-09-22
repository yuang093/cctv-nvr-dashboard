"""
tests/test_docs_factcheck.py
==============================
Week 8 Issue #025 — 文件 fact-check 守門員測試。

驗證 scripts/docs_factcheck.py 對齊 5 項檢查：
  1. routes 數（api_endpoints.md §2.1 表 vs 程式碼實際 routes）
  2. 表名（database_schema.md vs db/migrations/*.py / db/sqlite_writer.py）
  3. OpenAPI YAML 數（docs/openapi-migration.md vs 實際 YAML 數）
  4. pytest 數（CHANGELOG.md / overview.md 提到數字 vs 實際 pytest 收集）
  5. .bat / .sh / .ps1 入口腳本（DEPLOY.md 提到 vs 實際檔案）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 確保 scripts 目錄可 import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from docs_factcheck import (  # noqa: E402
    check_routes_in_api_endpoints,
    check_table_names_in_schema_doc,
    check_openapi_yaml_count,
    check_pytest_count_in_docs,
    check_entry_scripts,
)


def test_factcheck_routes_in_api_endpoints():
    """api_endpoints.md §2.1 應標註 OpenAPI 自動產生取代。"""
    drift = check_routes_in_api_endpoints()
    assert drift == [], f"api_endpoints.md 漂移：{drift}"


def test_factcheck_table_names_in_schema_doc():
    """database_schema.md 提到的表名都應實際存在於 sqlite_writer._init_schema。"""
    drift = check_table_names_in_schema_doc()
    assert drift == [], f"database_schema.md 漂移：{drift}"


def test_factcheck_openapi_yaml_count():
    """openapi-migration.md 提到 YAML 數，應與實際一致。"""
    drift = check_openapi_yaml_count()
    assert drift == [], f"openapi-migration.md 漂移：{drift}"


def test_factcheck_pytest_count_in_docs():
    """CHANGELOG.md 與 overview.md 提到的 pytest 數應與實際一致。"""
    drift = check_pytest_count_in_docs()
    assert drift == [], f"CHANGELOG.md / overview.md pytest 數漂移：{drift}"


def test_factcheck_entry_scripts():
    """DEPLOY.md 提到的入口腳本應實際存在。"""
    drift = check_entry_scripts()
    assert drift == [], f"DEPLOY.md 入口腳本漂移：{drift}"
