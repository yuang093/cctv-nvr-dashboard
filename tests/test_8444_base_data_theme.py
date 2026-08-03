"""驗證 8444 base.html 引入 tokens.css 並設置 data-theme 屬性。"""
from pathlib import Path


BASE_HTML = Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"


def test_base_html_has_data_theme_attribute():
    """<html> 應有 data-theme 屬性，依 dark flag 切換。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert 'data-theme="{% if dark %}dark{% else %}light{% endif %}"' in content, \
        "應有 <html data-theme=\"...\" 動態屬性"


def test_base_html_links_tokens_css():
    """應引入 tokens.css。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert "tokens.css" in content, "應引入 web/static/css/tokens.css"
    bootstrap_pos = content.find("bootstrap.min.css")
    tokens_pos = content.find("tokens.css")
    assert tokens_pos < bootstrap_pos, "tokens.css 應在 Bootstrap 之前引入"


def test_base_html_keeps_existing_dark_toggle():
    """既有 dark toggle 按鈕與 fintech-dark.css 引入都要保留。"""
    content = BASE_HTML.read_text(encoding="utf-8")
    assert "dark_toggle" in content, "dark_toggle 路由要保留"
    assert "fintech-dark.css" in content, "fintech-dark.css 引入要保留"