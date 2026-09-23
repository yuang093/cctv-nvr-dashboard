"""Week 6 #017 — Blueprint 拆分（8444 dashboard）。

業務領域切分（5 bp）：
- dashboard_bp    儀表板首頁 + 主題
- runs_bp         跑列表 / 跑詳情 / 報告下載 / ad-hoc SQL
- nvrs_bp         NVR 管理（list / CRUD / import / export / 連線測試）
- scan_bp         背景掃描觸發 / 狀態輪詢 / timeline refresh（+ module-level state）
- devices_bp      相機牆 / 設備總覽 / 探索 / 詳情 / 健康歷史 / 趨勢 / 事件列表

工廠模式：
    create_app() 在 web/app.py 內 register_blueprint() 全部。

設計重點（Week 6 Plan §D2）：
- Module-level state（_scan_state / _timeline_state）仍保留 module-level，
  只不過從 web/app.py 搬到 web/blueprints/scan_bp.py 模組檔頭，
  process 級全域、跨執行緒可見，行為零變。
- 共用 helpers（_safe_int / _to_taipei_str / taipei filter）位於 web/helpers.py。
- Week 5 middleware（auth / ratelimit / audit）由 web/app.py factory 主幹註冊，
  與 bp 順序無關。
"""
