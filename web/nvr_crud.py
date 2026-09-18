"""
web/nvr_crud.py
===============
NVR CRUD 共用 helper（給 8444 dashboard 與 8555 clips app 共用）。

2026-07-13 Phase 3 重構：從 web/app.py 與 web/nvr_routes.py 抽出 DRY 重複程式碼。
提供：
- parse_nvr_form(form)             # 從 request.form 解析 NVR 欄位
- normalize_nvr_dict(raw)          # CSV/JSON 來的 dict 正規化
- parse_csv(content)               # 解析 CSV 文字
- parse_json(content)              # 解析 JSON 文字
- detect_format(filename, content) # 副檔名偵測
- safe_int(value, default, ...)    # 安全的 int 解析（給 page 參數用）
- CSV_FIELDS                       # CSV 欄位順序常數
"""

from __future__ import annotations

import csv
import io
import json


# === CSV 欄位順序（給 export 用）===
CSV_FIELDS = [
    "id",
    "name",
    "host",
    "port",
    "username",
    "password",
    "verify_ssl",
    "site_id",
    "tags",
]


# === Form 解析 / 正規化 helpers ===


def parse_nvr_form(form) -> dict:
    """從 request.form 解析 NVR 欄位，做基本驗證。

    Raises:
        ValueError: 欄位缺失或格式錯誤。
    """
    data = {
        "nvr_id": (form.get("nvr_id") or "").strip(),
        "name": (form.get("name") or "").strip(),
        "host": (form.get("host") or "").strip(),
        "port": (form.get("port") or "8443").strip(),
        "username": (form.get("username") or "").strip(),
        "password": form.get("password") or "",  # 不 strip（密碼可能有空白）
        "verify_ssl": form.get("verify_ssl") == "on",
        "site_id": (form.get("site_id") or "").strip(),
        "tags": (form.get("tags") or "").strip(),
    }
    if not data["nvr_id"]:
        raise ValueError("ID 必填")
    if not data["name"]:
        raise ValueError("名稱必填")
    if not data["host"]:
        raise ValueError("Host 必填")
    if not data["username"]:
        raise ValueError("Username 必填")
    try:
        port = int(data["port"])
        if not (1 <= port <= 65535):
            raise ValueError
        data["port"] = port
    except ValueError:
        raise ValueError(f"Port 必須是 1-65535 的數字（收到：{data['port']}）")
    return data


def normalize_nvr_dict(raw: dict) -> dict:
    """把 CSV/JSON 來的 dict 標準化為 create_nvr 接受的格式。"""
    out = dict(raw)
    if "id" in out and "nvr_id" not in out:
        out["nvr_id"] = out.pop("id")
    # 必填（password 為選填 — upsert 模式下留空代表「保留原密碼」）
    for field in ("nvr_id", "name", "host", "username"):
        if not str(out.get(field, "")).strip():
            raise ValueError(f"必填欄位「{field}」為空")
    # 整數欄位
    try:
        out["port"] = int(out.get("port", 8443))
        if not (1 <= out["port"] <= 65535):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(f"port 必須是 1-65535 的整數（收到：{out.get('port')!r}）")
    # verify_ssl → bool
    vs = out.get("verify_ssl", False)
    if isinstance(vs, str):
        out["verify_ssl"] = vs.strip().lower() in ("1", "true", "yes", "y", "on")
    else:
        out["verify_ssl"] = bool(vs)
    # tags → list
    tags = out.get("tags", [])
    if isinstance(tags, str):
        out["tags"] = [t.strip() for t in tags.split(";") if t.strip()]
    elif not isinstance(tags, list):
        out["tags"] = []
    # site_id 留空字串 → None
    if not out.get("site_id"):
        out["site_id"] = None
    return out


def parse_csv(content: str) -> tuple[list[dict], list[str]]:
    """解析 CSV 文字。回傳 (parsed_list, errors)。

    自動偵測 delimiter（`,` / `\\t`）：
        - Excel 預設用 `,`；中文 Windows 下「另存 CSV」有時變 tab
        - 用 `csv.Sniffer` 從 header 推測；失敗時 fallback `,`
    """
    errors: list[str] = []
    parsed: list[dict] = []
    sample = content[:4096]
    delimiter = ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        delimiter = dialect.delimiter
    except csv.Error:
        pass
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    if not reader.fieldnames or "id" not in reader.fieldnames:
        errors.append(
            f"CSV 缺少必要欄位「id」"
            f"（delimiter={delimiter!r}，找到：{reader.fieldnames}）"
        )
        return parsed, errors
    for line_no, row in enumerate(reader, start=2):  # start=2（line 1 是 header）
        try:
            parsed.append(normalize_nvr_dict(row))
        except ValueError as e:
            errors.append(f"第 {line_no} 行：{e}")
    return parsed, errors


def parse_json(content: str) -> tuple[list[dict], list[str]]:
    """解析 JSON 文字。回傳 (parsed_list, errors)。"""
    errors: list[str] = []
    parsed: list[dict] = []
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        errors.append(f"JSON 語法錯誤：{e}")
        return parsed, errors
    if not isinstance(data, list):
        errors.append("JSON 必須是 array（[...]），不是 object")
        return parsed, errors
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {idx} 筆：不是 object")
            continue
        try:
            parsed.append(normalize_nvr_dict(item))
        except ValueError as e:
            errors.append(f"第 {idx} 筆：{e}")
    return parsed, errors


def detect_format(filename: str, content: str) -> str:
    """從檔名或內容推斷格式。"""
    if filename:
        low = filename.lower()
        if low.endswith(".csv"):
            return "csv"
        if low.endswith(".json"):
            return "json"
    stripped = content.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return "json"
    return "csv"


# === Safe int helper（給 page 參數用）===


def safe_int(value, default: int, *, min_val: int = 0, max_val: int = 2**31) -> int:
    """安全的 int 解析。失敗或超出範圍時回 default / 邊界值。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if result < min_val:
        return min_val
    if result > max_val:
        return max_val
    return result


# === 向下相容：保留舊名（_ 開頭）===
# 2026-07-13 Phase 3：保留舊 import 路徑，給 web/app.py 與 web/nvr_routes.py
# 漸進式遷移用。下個版本可移除。
_parse_nvr_form = parse_nvr_form
_normalize_nvr_dict = normalize_nvr_dict
_parse_csv = parse_csv
_parse_json = parse_json
_detect_format = detect_format
_safe_int = safe_int
_CSV_FIELDS = CSV_FIELDS
