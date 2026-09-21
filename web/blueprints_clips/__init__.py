"""Week 6 #018 — Blueprint 拆分（8555 clips）。

業務領域切分（3 bp）：
- pages_bp     頁面 alias（`/`、`/clips`）
- coverage_bp  Spec F 錄影覆蓋熱區（`/clips/coverage` + data）
- media_bp     Media API（`/clips/nvrs` `/cameras` `/snapshots` `/fetch` `/fetch_sync`）

`nvr_bp` 已存在於 web/nvr_routes.py（Phase 2.7 補）。
"""
