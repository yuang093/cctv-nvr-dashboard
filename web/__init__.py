"""
web/
====
v2 Web UI（Flask 唯讀前端）。

啟動：python -m web.app
預設：http://127.0.0.1:5000

設計：
    - 唯讀（v1 不開放寫入 UI，避免與 background worker 競爭）
    - 無登入（部署時建議綁 127.0.0.1 或反向代理加 auth）
    - 共享 SqliteWriter schema，但 web 端開新連線用 raw read-only query
"""
