"""驗證 clips.html 處理 URL params（user 031.PNG：coverage 點綠帶跳轉後直接顯示片段）。

防呆：
- 必須讀 nvr_id / cam_id / t
- 必須自動選 NVR / 勾 cam / 填時間
- 三者齊全時必須自動觸發同步撥放
"""

from pathlib import Path


CLIPS_HTML = (
    Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "clips.html"
)


def test_clips_html_reads_urlsearchparams():
    """clips.html 必須用 URLSearchParams 讀 query string。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert (
        "new URLSearchParams(location.search)" in content
    ), "應用 URLSearchParams 讀 ?nvr_id=...&cam_id=...&t=..."


def test_clips_html_reads_nvr_id_cam_id_t():
    """必須讀三個 param：nvr_id / cam_id / t。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert 'urlParams.get("nvr_id")' in content, "應讀 nvr_id"
    assert 'urlParams.get("cam_id")' in content, "應讀 cam_id"
    assert 'urlParams.get("t")' in content, "應讀 t"


def test_clips_html_auto_selects_nvr():
    """載入 NVR 清單後應自動選中 URL 帶的 nvr_id。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "$nvr.value = pendingNvr" in content, "應自動設定 $nvr.value"
    assert (
        '$nvr.dispatchEvent(new Event("change"))' in content
    ), "應 trigger change 載 cameras"


def test_clips_html_auto_checks_cam():
    """cameras 載入後應自動勾選 URL 帶的 cam_id。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "targetCb.checked = true" in content, "應自動勾選對應 cam checkbox"


def test_clips_html_auto_plays_when_all_three_params_present():
    """三者齊全時應自動觸發同步撥放。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "autoPlayPending" in content, "應有 autoPlayPending 旗標"
    assert "$syncPlayBtn.click()" in content, "應自動 click 同步撥放按鈕"


def test_clips_html_fills_datetime_local_from_param():
    """URL 帶的 t 應填入 datetime-local input。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "$t.value = pendingT" in content, "應把 t 填入 datetime-local"


def test_clips_html_handles_invalid_t_gracefully():
    """t 格式不對（不是 datetime-local）不應爆掉，應忽略。"""
    import re

    content = CLIPS_HTML.read_text(encoding="utf-8")
    # 期待有 regex 驗 datetime-local 格式（pattern literal）
    pattern = re.compile(r"/\^\\d\{4\}-\\d\{2\}-\\d\{2\}T\\d\{2\}:\\d\{2\}/")
    assert pattern.search(
        content
    ), "應用 regex 驗 t 格式（literal '\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}'），無效就忽略"


def test_clips_html_uses_css_escape_for_selector():
    """device_id / internal_id 可能有特殊字元，selector 必須 escape。"""
    content = CLIPS_HTML.read_text(encoding="utf-8")
    assert "cssEscape" in content, "selector 應用 cssEscape 避免 injection"
