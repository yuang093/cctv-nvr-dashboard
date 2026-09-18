"""
test_clips_sync_logic.py
========================
驗證 clips.html 內的同步撥放邏輯（2026-07-13 user 要求「播放同樣秒數」）。

注意：sync 邏輯是 client-side JS 混在 Jinja template 的 <script> 內，
純 pytest 跑不起來。所以用「靜態結構測試」確認關鍵 code path 都在。

涵蓋的關鍵行為：
- 用 requestAnimationFrame 做 sync loop
- 找 master（第一個 playing 且有 src 的 video）
- 校正閾值 0.3s（差距 > 0.3s 才校正，避免每 frame 校正造成畫面閃爍）
- 已 ended 的 video 不被校正
- 已 paused 的 video 不被當 master 也不被校正
- 接近 duration 結尾的 video 不被校正到超出實際長度
- closeAllSyncSlots 時停止 sync loop
- status bar 顯示「已校正 N 次」讓 user 看得到 sync 在運作
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# clips.html 完整路徑（給靜態 grep 用）
TEMPLATE = (
    Path(__file__).resolve().parent.parent / "web" / "clips_templates" / "clips.html"
)


@pytest.fixture(scope="module")
def template_text() -> str:
    """一次讀完 template，給所有測試共用。"""
    assert TEMPLATE.exists(), f"找不到 template：{TEMPLATE}"
    return TEMPLATE.read_text(encoding="utf-8")


def _extract_script(template: str) -> str:
    """抽出整個 <script>...</script> 區塊內容。"""
    m = re.search(r"<script>(.*?)</script>", template, re.DOTALL)
    assert m, "找不到 <script> 區塊"
    return m.group(1)


class TestSyncLoopExists:
    """確認 sync loop 的核心函數都已定義。"""

    def test_startSyncLoop_defined(self, template_text):
        assert "function startSyncLoop()" in template_text

    def test_stopSyncLoop_defined(self, template_text):
        assert "function stopSyncLoop()" in template_text

    def test_startSyncLoop_called_from_renderSyncSlots(self, template_text):
        """renderSyncSlots 後必須啟動 sync loop。"""
        # 抓整段 renderSyncSlots 函式（從宣告到下個 function 之前）
        body = _slice_function(template_text, "renderSyncSlots")
        assert "startSyncLoop()" in body, "renderSyncSlots 必須呼叫 startSyncLoop()"

    def test_stopSyncLoop_called_from_closeAllSyncSlots(self, template_text):
        """closeAllSyncSlots 必須停 sync loop，避免背景繼續校正。"""
        body = _slice_function(template_text, "closeAllSyncSlots")
        assert "stopSyncLoop()" in body, "closeAllSyncSlots 必須呼叫 stopSyncLoop()"


def _slice_function(template: str, name: str) -> str:
    """從 template 抽出「function <name>(...) {...}」的整段 body。

    從 `function <name>` 行開始，到下一個**頂層**獨立 function 宣告為止。
    頂層縮排 = 4 空格以內（內部 callback 通常 ≥ 8 空格）。
    """
    start = template.index(f"function {name}(")
    # 找下一個**頂層** function 宣告（縮排 ≤4 空格）
    # 避免被內部 `.then(function(r) {})` callback 誤抓
    rest = template[start + len(f"function {name}(") :]
    end_m = re.search(r"\n {0,4}function\s+\w+\s*\(", rest)
    end = end_m.start() if end_m else len(rest)
    return rest[:end] if not end_m else rest[: end_m.start()] + ""


class TestSyncLoopMechanics:
    """確認 sync loop 內的關鍵行為（master 選擇、校正條件）。"""

    def test_uses_requestAnimationFrame(self, template_text):
        """sync loop 必須用 requestAnimationFrame，不是 setInterval。"""
        script = _extract_script(template_text)
        # startSyncLoop 內有 requestAnimationFrame
        m = re.search(
            r"function startSyncLoop\(\).*?function tick.*?\}",
            script,
            re.DOTALL,
        )
        assert m, "找不到 startSyncLoop + tick"
        body = m.group(0)
        assert "requestAnimationFrame(tick)" in body, "tick 必須遞迴註冊 RAF"
        assert "requestAnimationFrame(tick);" in body

    def test_cancelAnimationFrame_on_stop(self, template_text):
        """stopSyncLoop 必須用 cancelAnimationFrame 停掉 loop。"""
        m = re.search(
            r"function stopSyncLoop\(\)\s*\{(.*?)\n\s*\}", template_text, re.DOTALL
        )
        assert m
        body = m.group(1)
        assert "cancelAnimationFrame" in body

    def test_master_selection_skips_paused(self, template_text):
        """找 master 時必須跳過 paused 的 video。"""
        script = _extract_script(template_text)
        # 找「找 master」的 for 迴圈（2026-07-14 把 `var master` 改成 `_master`，
        # 提到 closure 範圍讓 masterTimeStr 抓得到，所以 regex 也用 _master）
        m = re.search(
            r"// 找 master：(.*?)}\s*if \(!_master\) return;",
            script,
            re.DOTALL,
        )
        assert m
        body = m.group(1)
        assert "v.paused" in body, "master 選擇必須跳過 paused video"
        assert "v.ended" in body, "master 選擇必須跳過 ended video"
        assert "isFinite(v.duration)" in body, "master 必須有有效 duration"

    def test_correction_threshold_is_0_3_seconds(self, template_text):
        """校正條件差距 > 0.3s 才校正（防止每 frame 校正造成畫面閃爍）。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        assert m
        body = m.group(1)
        # 0.3 必須寫死在差距判斷裡
        assert (
            "Math.abs(v.currentTime - mt) > 0.3" in body
        ), "校正閾值必須是 0.3s（防止過度校正）"

    def test_corrects_slave_currentTime_to_master(self, template_text):
        """slave.currentTime 必須被設成 mt（master.currentTime）。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        body = m.group(1)
        assert "v.currentTime = mt;" in body, "校正時必須把 slave.currentTime 設成 mt"

    def test_ended_slave_not_corrected(self, template_text):
        """已播完的 video 不被校正（保留在尾端）。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        body = m.group(1)
        # slave 處理區段要跳過 ended
        assert "v.paused || v.ended" in body, "slave 校正前必須跳過 paused/ended video"

    def test_near_end_not_corrected_beyond_duration(self, template_text):
        """接近結尾的 video 不被校正（避免 currentTime 超過實際 duration）。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        body = m.group(1)
        # 必須有「currentTime >= duration - 某個 buffer」的 guard
        assert (
            "dur - 0.15" in body or "duration - 0.15" in body
        ), "接近結尾必須有 guard（dur - 0.15 或類似）"

    def test_actual_dur_used_not_video_duration(self, template_text):
        """sync loop 用 data-actual-dur（X-Actual-Duration header 帶回的 NVR 真實長度），
        不是 video.duration（瀏覽器讀完 metadata 才會有，會延遲）。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        body = m.group(1)
        assert (
            "dataset.actualDur" in body
        ), "必須讀 data-actual-dur（X-Actual-Duration header），這才是 NVR 真實長度"

    def test_throttle_loop_to_150ms(self, template_text):
        """loop 必須 throttle 到約 150ms（不要每 16ms 都校正一次）。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        body = m.group(1)
        # 找 throttle 邏輯
        assert (
            "now - _lastTickMs < 150" in body or "_lastTickMs < 150" in body
        ), "必須 throttle 到 150ms 以下頻率"


class TestSyncUI:
    """確認 UI 顯示「同步狀態」+ video tag 有正確 data attribute。"""

    def test_status_bar_shows_correction_count(self, template_text):
        """status bar 必須顯示「已校正 N 次」讓 user 看得到 sync 在運作。"""
        assert "已校正" in template_text and "_syncCorrections" in template_text

    def test_video_tag_has_data_slot_idx(self, template_text):
        """video tag 必須有 data-slot-idx attribute 給 JS 識別 slot。"""
        assert "data-slot-idx" in template_text

    def test_video_tag_has_data_actual_dur(self, template_text):
        """video tag 必須有 data-actual-dur attribute 帶 NVR 真實長度。"""
        assert "data-actual-dur" in template_text

    def test_sync_badge_present(self, template_text):
        """每個 video 旁邊要有 sync-badge（顯示同步狀態）。"""
        assert "sync-badge" in template_text


class TestVideoAspectRatio:
    """2026-07-13 user 回報「影片切到邊邊」——

    修法：
    - 拿掉 .sync-slot 硬寫的 aspect-ratio: 4/3（NVR cam 不一定是 4:3）
    - video 改用 width:100% + height:auto + object-fit:contain
    - JS 偵測 videoWidth/Height，動態設 video.style.aspectRatio
    """

    def test_no_hardcoded_aspect_ratio_4_3_on_slot(self, template_text):
        """確認 .sync-slot 沒有硬寫 aspect-ratio: 4/3。"""
        # 找 .sync-slot CSS block（非註解）
        m = re.search(r"\.sync-slot\s*\{[^}]+\}", template_text)
        assert m, "找不到 .sync-slot CSS"
        block = m.group(0)
        # 拿掉整行註解再檢查
        no_comments = "\n".join(
            line
            for line in block.split("\n")
            if not line.strip().startswith("/*") and "*/" not in line
        )
        assert (
            "aspect-ratio" not in no_comments
        ), f".sync-slot 不應該硬寫 aspect-ratio（會切到邊邊）：\n{block}"

    def test_video_css_uses_height_auto_not_100(self, template_text):
        """video CSS 不該用 height: 100%（會強制撐滿破壞比例）。"""
        m = re.search(r"\.sync-slot\s+\.sync-video\s*\{[^}]+\}", template_text)
        assert m, "找不到 .sync-video CSS"
        block = m.group(0)
        assert (
            "height: 100%" not in block
        ), f"video 不應該用 height:100%，會破壞比例切到邊邊：\n{block}"
        assert "height: auto" in block, "video 必須用 height: auto 維持比例"

    def test_video_uses_object_fit_contain(self, template_text):
        """video 必須 object-fit: contain 才不切邊。"""
        m = re.search(r"\.sync-slot\s+\.sync-video\s*\{[^}]+\}", template_text)
        block = m.group(0)
        assert "object-fit: contain" in block, "video 必須 object-fit: contain"

    def test_loadedmetadata_handler_attached(self, template_text):
        """video 必須有 loadedmetadata listener 才能動態偵測真實比例。"""
        assert "loadedmetadata" in template_text
        # listener 必須讀 videoWidth/Height 才有意義
        assert "videoWidth" in template_text
        assert "videoHeight" in template_text

    def test_dynamic_aspect_ratio_set_on_video(self, template_text):
        """偵測到真實比例後必須設到 video.style.aspectRatio。"""
        assert "this.style.aspectRatio" in template_text or (
            "style.aspectRatio" in template_text
        ), "必須把偵測到的比例設到 video 的 inline style"


class TestMasterTimeStr:
    """確認 status bar 顯示的 master time 格式正確（m:ss）。"""

    def test_master_time_formatted_m_ss(self, template_text):
        """master time 必須格式化為 m:ss。"""
        m = re.search(
            r"function startSyncLoop\(\)(.*?)function stopSyncLoop",
            template_text,
            re.DOTALL,
        )
        body = m.group(1)
        # 找 masterTimeStr 函式內的格式化邏輯
        assert "masterTimeStr" in body
        # 必須用 floor + Math.floor 分鐘秒鐘
        assert "Math.floor(t / 60)" in body or "Math.floor(t/60)" in body
        assert "Math.floor(t % 60)" in body or "Math.floor(t%60)" in body


class TestNoRecordingVsNvrError:
    """2026-07-14 user 要求「時段無錄影顯示友善訊息，分開 NVR 故障」。

    Server 端（web/clips_app.py）：
    - NO_RECORDING → 404 + error: "NO_RECORDING" + message: "此時段無錄影資料"
    - NVR_INTERNAL_ERROR → 502 + error: "NVR_INTERNAL_ERROR" + detail
    - AUTH_FAILED → 502 + error: "AUTH_FAILED"

    Client 端（web/clips_templates/clips.html）：
    - fetchClipForCamera 把 server JSON 完整傳上來（err.code）
    - renderSyncSlots 對 NO_RECORDING / EMPTY_CLIP 用 .sync-slot.no-recording class
    - 其他 error 用 .sync-slot.error class + ⚠️ 圖示
    """

    def test_fetch_error_preserves_code(self, template_text):
        """fetchClipForCamera 收到 error 時必須把 server 的 error 當 err.code。"""
        body = _slice_function(template_text, "fetchClipForCamera")
        # 必須有「var err = new Error(j.error) ... err.code = j.error」
        # （Server JSON 的 error 欄位當作 code 給 UI 用）
        assert (
            "err.code" in body
        ), "fetchClipForCamera 必須把 server JSON 的 error 欄位存成 err.code"
        assert "j.error" in body

    def test_no_recording_class_defined(self, template_text):
        """CSS 必須定義 .sync-slot.no-recording（灰色 + dashed border，友善樣式）。"""
        assert ".sync-slot.no-recording" in template_text

    def test_error_display_uses_warning_emoji(self, template_text):
        """NVR 故障 slot 改用 ⚠️（不是 📭），讓 user 區分紅框錯誤 vs 灰色無資料。"""
        body = _slice_function(template_text, "renderSyncSlots")
        # 在 error 路徑必須有 ⚠️
        assert "⚠️" in body, "NVR 故障應顯示 ⚠️ 圖示"

    def test_no_recording_slot_text(self, template_text):
        """NO_RECORDING slot 必須顯示「此時段無錄影資料」字串。"""
        body = _slice_function(template_text, "renderSyncSlots")
        assert "此時段無錄影資料" in body, "無錄影訊息應顯示「此時段無錄影資料」字串"

    def test_no_recording_branch_uses_errorCode(self, template_text):
        """renderSyncSlots 必須依 errorCode 判斷 NO_RECORDING / EMPTY_CLIP。"""
        body = _slice_function(template_text, "renderSyncSlots")
        # 必須有 's.errorCode === "NO_RECORDING"' 之類的判斷
        assert "NO_RECORDING" in body, "必須判斷 errorCode === 'NO_RECORDING'"
        assert "EMPTY_CLIP" in body, "必須判斷 errorCode === 'EMPTY_CLIP'"

    def test_sync_slot_error_code_stored(self, template_text):
        """catch 區塊必須把 e.code 存到 syncSlots[i].errorCode。"""
        body = _slice_function(template_text, "startSyncPlay")
        assert (
            "errorCode" in body
        ), "startSyncPlay 的 catch 必須存 errorCode 給 render 用"

    def test_nvr_connection_failed_label(self, template_text):
        """NVR 故障 slot 必須顯示「NVR 連線失敗」字串區分無錄影。"""
        body = _slice_function(template_text, "renderSyncSlots")
        assert "NVR 連線失敗" in body, "NVR 故障 slot 應顯示「NVR 連線失敗」標籤"


class TestFetchSyncIntersection:
    """2026-07-14 方案 A：2+ 台 cam 同步撥放用交集計算（fetch_sync）。

    Server: /clips/fetch_sync 回 multipart/mixed，每段 metadata 在 X-* headers。
    Frontend: clips.html 用 fetchSyncClips + parseMultipartBytes 處理。

    注意：以下測試用全文 grep，不用 _slice_function（後者處理 async function
    邊界時 regex 會誤抓內部 .then / .catch callback）。
    """

    def test_fetch_sync_endpoint_call(self, template_text):
        """全文必須有 /clips/fetch_sync 端點呼叫。"""
        assert "/clips/fetch_sync" in template_text, "必須使用 /clips/fetch_sync 端點"

    def test_target_seconds_240(self, template_text):
        """±2 分鐘 = 240s 視窗（user 2026-07-14 決定，從 60s 拉長到 240s）。"""
        assert "target_seconds" in template_text
        assert "240" in template_text, "必須用 240 秒視窗（±2 分鐘）"

    def test_parse_multipart_bytes_function_exists(self, template_text):
        """必須有 parseMultipartBytes 函式處理 server 的 multipart response。"""
        assert "function parseMultipartBytes" in template_text

    def test_find_bytes_helper_defined(self, template_text):
        """byte-level 搜尋 helper（findBytes）必須存在。"""
        assert "function findBytes" in template_text

    def test_fetch_sync_clips_async(self, template_text):
        """fetchSyncClips 必須是 async 才能 await blob().arrayBuffer()。"""
        assert "async function fetchSyncClips" in template_text

    def test_no_common_recording_branch_in_sync(self, template_text):
        """當 fetch_sync 回 NO_COMMON_RECORDING 時，前端要有專屬分支。"""
        # 在某個 .catch 裡面 e.code === "NO_COMMON_RECORDING" 的判斷
        assert (
            "NO_COMMON_RECORDING" in template_text
        ), "必須判斷 NO_COMMON_RECORDING 顯示友善訊息"

    def test_single_cam_uses_fetch_clip(self, template_text):
        """1 台 cam 時仍走舊 fetchClipForCamera（不用交集）。"""
        assert (
            "selectedCams.length === 1" in template_text
        ), "1 台時不需交集計算，直接 fetchClipForCamera"

    def test_one_cam_2_minute_window(self, template_text):
        """1 台時也用 ±2 分鐘視窗（2026-07-14 user 從 ±30s 拉長到 ±2m）。"""
        assert (
            "fetchClipForCamera(selectedCams[0], syncCenterIso, 120)" in template_text
            or "fetchClipForCamera(selectedCams[0], syncCenterIso, 120,"
            in template_text
        ), "1 台 cam 必須用 ±2 分鐘視窗（halfWindowSec=120）"

    def test_multipart_parser_handles_crlfcrlf(self, template_text):
        """parseMultipartBytes 必須找 \\r\\n\\r\\n 分隔 headers 跟 body。"""
        assert (
            "\\r\\n\\r\\n" in template_text or "CRLFCRLF" in template_text
        ), "parser 必須用 \\r\\n\\r\\n 作為 headers/body separator"

    def test_multipart_parser_skips_end_boundary(self, template_text):
        """parseMultipartBytes 必須跳過最後一段（end boundary）。"""
        assert (
            "end" in template_text.lower() and "skip" in template_text.lower()
        ) or "end marker" in template_text.lower(), "parser 必須跳過最後 end boundary"

    def test_status_shows_overlap_for_success(self, template_text):
        """成功後 status 應顯示交集長度（讓 user 知道實際播多長）。"""
        # 全文要有「交集」字串 + intersectionLength 變數
        assert "交集" in template_text, "成功後 status 必須提到「交集」字串"
        assert (
            "intersectionLength" in template_text
        ), "必須用 intersectionLength 變數顯示長度"
