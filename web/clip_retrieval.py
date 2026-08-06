"""
web/clip_retrieval.py
=====================
Phase 2.7 影片片段調閱：對應 NVR Media API 的 Protocol 與實作。

設計
----
* `MediaApiClient` Protocol：3 個 method（get_snapshot / get_mpd_manifest / fetch_clip），
  不負責登入，session token 由呼叫端注入。
* `MockMediaClient`：測試用，回固定 fixture，不打真 NVR。
* `MpdMediaClient`：真實 NVR 客戶端，純代理 `/mt/api/rest/v1/media`。
  完整實作等 NVR 上線時跑 `discover_media_api.py` 驗證後再補（spec 見
  `docs/media-api-research.md`）。

Port：8443（與 REST API 同一個 port，PDF 規格已驗證）
Auth：`?session=<token>`（沿用 `/login` 拿到的 token）
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterator, Protocol, runtime_checkable

import requests


# 固定 fixture 大小（給單元測試用 — 不用真 JPEG header 也沒關係，
# 只要回傳 byte 長度可被呼叫端驗證；Pillow 端測試在 test_clip_retrieval.py 補）
_MOCK_SNAPSHOT_BYTES = 1024   # 1 KB
_MOCK_MPD_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" minBufferTime="PT1.5S">'
    '<Period><AdaptationSet><Representation id="1" bandwidth="1000000" '
    'width="640" height="480" mimeType="video/mp4" codecs="avc1.4D4016">'
    '<BaseURL>/mt/api/rest/v1/media?ctx=MOCK&session=MOCK&cameraId=MOCK</BaseURL>'
    '</Representation></AdaptationSet></Period></MPD>'
)
_MOCK_CLIP_CHUNK = 4096   # 4 KB
_MOCK_CLIP_CHUNKS = 4     # 共 16 KB 假 mp4 stream


def _parse_iso8601_duration_to_seconds(dur: str) -> float:
    """Parse ISO 8601 duration (例 "PT9.390S" / "PT1M30.5S" / "PT2H") → 秒數 (float)。

    只處理 NVR Media API 會吐的格式（只有 P[n]DT[n]H[n]M[n]S，沒有 date 部分）。
    無法 parse → 回 0.0。
    """
    import re
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", dur)
    if not m:
        return 0.0
    h = int(m.group(1) or 0)
    mn = int(m.group(2) or 0)
    s = float(m.group(3) or 0)
    return h * 3600 + mn * 60 + s


@runtime_checkable
class MediaApiClient(Protocol):
    """對應 NVR Media API（/mt/api/rest/v1/media）。"""

    def get_snapshot(
        self,
        camera_id: str,
        at_time: datetime,    # UTC；NVR 用 ISO 8601 compact 格式
    ) -> bytes:
        """GET ?format=jpeg&t=<time> → 一張 JPEG bytes。"""

    def get_mpd_manifest(
        self,
        camera_id: str,
    ) -> str:
        """GET ?format=mpd → XML MPD 字串。"""

    def fetch_clip(
        self,
        camera_id: str,
        start_time: datetime,
        end_time: datetime,
        target_seconds: Optional[float] = None,
    ) -> Iterator[bytes]:
        """fragmented MP4 bytes 串流。

        target_seconds: 若給定，回傳 mp4 在該秒數截斷後的 prefix。
        對同步撥放（多 cam 同長度）場景必要；單機 optional。
        """

    def get_recording_duration(
        self,
        camera_id: str,
        at_time: datetime | str,
    ) -> float:
        """該 cam 在 at_time 附近有多少秒錄影（從 MPD manifest 解析）。

        對應 NVR endpoint：`GET /mt/api/rest/v1/media?format=mpd`
        回 MPD XML，從 `mediaPresentationDuration` 欄位解析秒數。

        對同步撥放（fetch_sync）必須：對每台 cam 查 duration → 算交集 → 抓 clip。
        """


class MockMediaClient:
    """單元測試 / 開發用：回固定 fixture，不打真 NVR。"""

    def __init__(
        self,
        *,
        snapshot_bytes: int = _MOCK_SNAPSHOT_BYTES,
        mpd_xml: str = _MOCK_MPD_XML,
        clip_chunk_size: int = _MOCK_CLIP_CHUNK,
        clip_chunks: int = _MOCK_CLIP_CHUNKS,
        recording_duration: float = 240.0,  # 2026-08-04 user 032.PNG：mock 預設 240s
    ) -> None:
        self._snapshot_size = snapshot_bytes
        self._mpd_xml = mpd_xml
        self._chunk_size = clip_chunk_size
        self._chunk_count = clip_chunks
        self._duration = recording_duration
        # 紀錄呼叫參數，測試可以驗證
        self.snapshot_calls: list[tuple[str, datetime]] = []
        self.manifest_calls: list[str] = []
        self.clip_calls: list[tuple[str, datetime, datetime]] = []
        self.duration_calls: list[tuple[str, datetime]] = []

    def get_snapshot(self, camera_id: str, at_time: datetime) -> bytes:
        self.snapshot_calls.append((camera_id, at_time))
        # 回傳長度固定的 dummy bytes；Pillow 在 thumbnail 路徑會用 BytesIO 讀
        return b"\x00" * self._snapshot_size

    def get_mpd_manifest(self, camera_id: str) -> str:
        self.manifest_calls.append(camera_id)
        return self._mpd_xml

    def fetch_clip(
        self,
        camera_id: str,
        start_time: datetime,
        end_time: datetime,
        target_seconds: Optional[float] = None,
    ) -> Iterator[bytes]:
        self.clip_calls.append((camera_id, start_time, end_time))
        for _ in range(self._chunk_count):
            yield b"\x00" * self._chunk_size

    def get_recording_duration(
        self,
        camera_id: str,
        at_time: datetime | str,
    ) -> float:
        # 2026-08-04 user 032.PNG：mock 必回 > 0，否則 /clips/fetch_sync 會把所有 cam
        # 都視為「無錄影」→ NO_COMMON_RECORDING → 前端誤顯示「NVR 連線失敗」
        self.duration_calls.append((camera_id, at_time))
        return self._duration


class MpdMediaClient:
    """真實 NVR Media API 客戶端（2026-07-07 實測可跑）。

    對應 NVR endpoint：`GET /mt/api/rest/v1/media?session=...&cameraId=...&format=...&t=...`
    探勘結果（見 `docs/mpd-sample.xml` + 本檔 commit 訊息）：
        - `format=jpeg` → 單張 image/jpeg 快照
        - `format=fmp4` → H.264 fragmented MP4 stream（video/mp4）
        - `format=mpd`  → DASH manifest（含多個 <Representation> + <BaseURL>，用於 seek）
    重要：`t` query 參數必須是 `live` 或 `YYYY-MM-DDTHH:MM:SS.fffZ`（**必須有 .fffZ**），
    違反會回 400 BAD_REQUEST，pattern 訊息會告訴你正確格式。
    """

    MEDIA_PATH = "/mt/api/rest/v1/media"
    SUPPORTED_FORMATS = ("mpd", "fmp4", "jpeg", "json", "webm", "spkc")
    # 內部 requests.Session（每 instance 一個；thread-safe 因為只用 GET）
    _DEFAULT_CHUNK = 64 * 1024  # 64KB chunks for streaming

    def __init__(
        self,
        *,
        host: str,
        port: int = 8443,
        session: str,
        verify_ssl: bool = False,
        timeout: int = 10,
    ) -> None:
        self._base_url = f"https://{host}:{port}{self.MEDIA_PATH}"
        self._session = session
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._http = requests.Session()
        # 不驗 SSL（自簽），跟 worker 一致
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _format_t(self, at_time: datetime | str) -> str:
        """把 datetime 轉成 NVR 吃的 `t` 參數（YYYY-MM-DDTHH:MM:SS.fffZ）。

        實測 pattern：`/^(live|((\\d{4})-?(\\d{2})-?(\\d{2})T(\\d{2}):?(\\d{2}):?(\\d{2})(\\.\\d{1,3}Z)(,[c,l,g,e])?)?)$/`
        沒毫秒 + 沒 Z 結尾就回 400。
        """
        if isinstance(at_time, str):
            # 允許 caller 直接傳已格式化好的（例 "live"）
            if at_time == "live":
                return "live"
            # ISO 8601 with Z but no millis → 補 .000Z
            if at_time.endswith("Z") and "." not in at_time:
                return at_time[:-1] + ".000Z"
            return at_time
        # datetime → 強制 UTC + 毫秒
        from datetime import timezone
        if at_time.tzinfo is None:
            at_time = at_time.replace(tzinfo=timezone.utc)
        return at_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{at_time.microsecond // 1000:03d}Z"

    def _get(self, fmt: str, camera_id: str, t: str) -> requests.Response:
        """單次 GET，回 requests.Response（caller 自己決定 read body / stream）。"""
        params = {
            "session": self._session,
            "cameraId": camera_id,
            "format": fmt,
            "t": t,
        }
        return self._http.get(
            self._base_url, params=params, timeout=self._timeout,
            verify=self._verify_ssl, stream=True,
        )

    def get_snapshot(self, camera_id: str, at_time: datetime) -> bytes:
        """拿單張 JPEG snapshot（at_time 附近那張 frame）。"""
        r = self._get("jpeg", camera_id, self._format_t(at_time))
        if r.status_code != 200:
            # 401 = session 過期（要 re-login），400 = camera id / t 格式錯
            raise RuntimeError(
                f"NVR get_snapshot 失敗 HTTP {r.status_code}：{r.text[:200]}"
            )
        return r.content

    def get_mpd_manifest(self, camera_id: str, at_time: datetime | str = "live") -> str:
        """拿 DASH MPD manifest XML（給需要 seek 的進階用途；v1 不一定用得到）。
        2026-08-06 修：401/403 raise NvrAuthError，讓 caller 偵測 stale session。
        """
        r = self._get("mpd", camera_id, self._format_t(at_time))
        if r.status_code != 200:
            body_preview = (r.text or "")[:200]
            if r.status_code in (401, 403):
                raise NvrAuthError(
                    f"NVR 認證失敗（{r.status_code}）：{body_preview}"
                )
            raise RuntimeError(
                f"NVR get_mpd_manifest 失敗 HTTP {r.status_code}：{body_preview}"
            )
        return r.content.decode("utf-8")

    def get_recording_duration(self, camera_id: str, at_time: datetime | str) -> float:
        """從 MPD manifest 解析 mediaPresentationDuration，回秒數（float）。

        NVR 不會用 404 表示「無錄影」；它會回 200 + MPD 但 duration 接近 0
        （或很短）。所以「無錄影」= duration < 1.0s。

        Returns:
            float 秒數（例 PT9.390S → 9.39）
        """
        import re
        mpd = self.get_mpd_manifest(camera_id, at_time)
        m = re.search(r'mediaPresentationDuration="([^"]+)"', mpd)
        if not m:
            return 0.0
        return _parse_iso8601_duration_to_seconds(m.group(1))

    def fetch_clip(
        self,
        camera_id: str,
        start_time: datetime,
        end_time: datetime,
        target_seconds: Optional[float] = None,
    ) -> Iterator[bytes]:
        """從 NVR 串流 mp4 clip（path B：直接 fmp4 stream，最簡單）。

        為什麼選 fmp4：path A (mpd 兩步) 需要 parse XML 抓 BaseURL，但 fmp4 單 GET
        就直接吐 video/mp4 stream；對「30 秒 clip」這個場景夠用且最簡。

        2026-07-14：加 target_seconds 參數。
        NVR fmp4 是 stream，沒有 end query 參數；server 拿到的是完整 mp4 串流。
        若 caller 想限制秒數（例如 2+ cam 交集計算後的 intersection_length），
        必須靠 _truncate_fmp4_chunks 在 server 端解析 mp4 box 結構截斷，
        不然前端 <video>.duration 仍是 mp4 本身長度，多 cam 同步會「影片長度不一」。

        Args:
            camera_id: 攝影機 ID
            start_time: UTC；對應 NVR `t=` 參數
            end_time: 保留位置（Protocol 相容性；目前沒用到）
            target_seconds: 若給定，回傳 mp4 在該秒數截斷後的 prefix bytes。
                           None → 原樣 yield 全部。

        Raises:
            NvrNoRecordingError: NVR 回 404 → 該時段無錄影
            NvrAuthError: NVR 回 401/403 → session 過期或無權限
            NvrInternalError: 其他 4xx/5xx → NVR 內部錯誤（前端紅框 ⚠️ 顯示）

        Yields: mp4 bytes chunks（64KB）；Flask 直接 stream 給 user。
        """
        r = self._get("fmp4", camera_id, self._format_t(start_time))
        if r.status_code != 200:
            # 2026-07-14 fix07.txt user 回報「分不清無錄影 vs NVR 故障」：
            # 區分 NVR 不同 status code 對應不同錯誤類型，前端用 errorCode
            # 判斷顯示「📭 此時段無錄影」或「⚠️ NVR 連線失敗」
            body_preview = (r.text or "")[:200]
            if r.status_code == 404:
                raise NvrNoRecordingError(
                    f"NVR 找不到此時段錄影（404）：{body_preview}"
                )
            elif r.status_code in (401, 403):
                raise NvrAuthError(
                    f"NVR 認證失敗（{r.status_code}）：{body_preview}"
                )
            else:
                raise NvrInternalError(
                    f"NVR fetch_clip 失敗 HTTP {r.status_code}：{body_preview}"
                )
        # stream=True → 用 iter_content 逐 chunk yield → 包進 truncation wrapper
        chunks = (chunk for chunk in r.iter_content(chunk_size=self._DEFAULT_CHUNK) if chunk)
        if target_seconds is not None:
            yield from _truncate_fmp4_chunks(chunks, target_seconds=target_seconds)
        else:
            yield from chunks
        r.close()


# === 2026-07-14：NVR 錯誤分類例外 ===
# 繼承 RuntimeError 讓既有 `except RuntimeError` fallback 仍可 catch，
# clips_app.py 用更精準的 `except NvrInternalError` 先處理 → else 漏給 RuntimeError。
# 注意 Python except 順序：精準的 (子類) 必須在通用 (RuntimeError) 之前才會生效。
class NvrNoRecordingError(RuntimeError):
    """NVR 回 404 — 該時段無錄影。前端顯示友善灰色訊息「此時段無錄影資料」。"""


class NvrAuthError(RuntimeError):
    """NVR 回 401/403 — session 過期或無權限。前端顯示紅框 ⚠️ + 建議重新登入。"""


class NvrInternalError(RuntimeError):
    """NVR 回其他 4xx/5xx — NVR 內部錯誤。前端顯示紅框 ⚠️「NVR 連線失敗」。"""


# === 2026-07-14：fmp4 truncation（解同步撥放秒數不一樣問題）===
# 原 fetch_clip 拿到的是 NVR 完整 stream（fmp4 是 stream，沒 end param），
# 即使 server 算交集給前端 metadata，前端 <video>.duration 仍 = mp4 本身長度。
# 解法：解析 fmp4 moof/tfdt 結構，找到「cumulative decode time ≥ target」的第一個 moof，
# 切掉該 moof 之前的內容當 prefix yield。
# 讓所有 cam 的 mp4 都是「交集長度」 → <video>.duration 一致 → 同步播到底。
import struct
from typing import Optional


def _parse_box_header(buf: bytes, off: int) -> Optional[tuple]:
    """Parse ISO BMFF box header at buf[off:].

    Returns (total_size, box_type_bytes, header_len) or None if not enough bytes.
    size==1 means next 8 bytes are 64-bit extended size.
    """
    if off + 8 > len(buf):
        return None
    size = struct.unpack(">I", buf[off:off+4])[0]
    btype = bytes(buf[off+4:off+8])
    if size == 1:
        if off + 16 > len(buf):
            return None
        size = struct.unpack(">Q", bytes(buf[off+8:off+16]))[0]
        return (size, btype, 16)
    if size < 8:
        return None  # malformed
    return (size, btype, 8)


def _find_subbox(buf: bytes, start: int, end: int, target_type: bytes) -> Optional[tuple]:
    """Find first direct child box of target_type inside buf[start:end]. Returns
    (off, header_len, total_size) or None. Not recursive.
    """
    off = start
    while off + 8 <= end:
        h = _parse_box_header(buf, off)
        if h is None:
            return None
        size, btype, hdr_len = h
        if size == 0:
            return None
        if btype == target_type:
            return (off, hdr_len, size)
        off += size
    return None


def _extract_moov_timescale(buf: bytes, moov_off: int, moov_size: int) -> Optional[int]:
    """從 moov 抽 timescale（moov/trak/mdia/mdhd.timescale）。

    timescale 用於把 baseMediaDecodeTime 換算成秒。
    """
    h = _find_subbox(buf, moov_off + 8, moov_off + moov_size, b"trak")
    if h is None:
        return None
    trak_off, _, trak_size = h
    h2 = _find_subbox(buf, trak_off + 8, trak_off + trak_size, b"mdia")
    if h2 is None:
        return None
    mdia_off, _, mdia_size = h2
    h3 = _find_subbox(buf, mdia_off + 8, mdia_off + mdia_size, b"mdhd")
    if h3 is None:
        return None
    mdhd_off, mdhd_hdr, mdhd_size = h3
    # mdhd fullbox: version(1) + flags(3) + creation_time + modification_time + timescale(4) + duration + language(2) + reserved(2)
    payload_start = mdhd_off + mdhd_hdr + 4  # after version + flags
    if payload_start + 4 > len(buf):
        return None
    version = buf[mdhd_off + mdhd_hdr]
    # version 0 → 4-byte timestamps; version 1 → 8-byte
    ts_size = 8 if version == 1 else 4
    timescale_off = payload_start + ts_size * 2
    if timescale_off + 4 > mdhd_off + mdhd_size:
        return None
    return struct.unpack(">I", bytes(buf[timescale_off:timescale_off+4]))[0]


def _extract_moof_tfdt(buf: bytes, moof_off: int, moof_size: int) -> Optional[int]:
    """從 moof 抽 baseMediaDecodeTime（moof/traf/tfdt）。

    回傳 ticks（用 moov.timescale 換算秒）。tfdt 是 fullbox。
    """
    h = _find_subbox(buf, moof_off + 8, moof_off + moof_size, b"traf")
    if h is None:
        return None
    traf_off, _, traf_size = h
    h2 = _find_subbox(buf, traf_off + 8, traf_off + traf_size, b"tfdt")
    if h2 is None:
        return None
    tfdt_off, tfdt_hdr, tfdt_size = h2
    if tfdt_off + tfdt_hdr + 4 > len(buf):
        return None
    version = buf[tfdt_off + tfdt_hdr]
    value_off = tfdt_off + tfdt_hdr + 4  # version + flags
    if version == 1:
        if value_off + 8 > tfdt_off + tfdt_size:
            return None
        return struct.unpack(">Q", bytes(buf[value_off:value_off+8]))[0]
    if value_off + 4 > tfdt_off + tfdt_size:
        return None
    return struct.unpack(">I", bytes(buf[value_off:value_off+4]))[0]


def _find_fmp4_cut_point(buf: bytes, target_seconds: float) -> Optional[int]:
    """Walk fmp4 boxes; return byte offset where to cut so cumulative decode time
    <= target_seconds. Returns None if no moov+tfdt found (then caller can use
    byte-budget fallback).

    策略：累積每個 moof 的 tfdt，超過 target 就停在「前一個 moof 結束處」。
    """
    timescale: Optional[int] = None
    target_ticks: Optional[int] = None
    first_tfdt: Optional[int] = None
    last_safe_cut = 0  # offset where last fully-contained fragment ends
    pos = 0
    has_any_moof = False

    while pos + 8 <= len(buf):
        h = _parse_box_header(buf, pos)
        if h is None:
            break
        size, btype, hdr_len = h
        if size == 0 or size < hdr_len:
            break
        if pos + size > len(buf):
            break  # truncated box → stop

        if btype == b"moov":
            ts = _extract_moov_timescale(buf, pos, size)
            if ts and ts > 0:
                timescale = ts
                target_ticks = int(round(target_seconds * ts))
        elif btype == b"moof" and timescale and target_ticks:
            cur = _extract_moof_tfdt(buf, pos, size)
            if cur is not None:
                if first_tfdt is None:
                    first_tfdt = cur
                elapsed = cur - first_tfdt
                if elapsed > target_ticks:
                    return last_safe_cut
                last_safe_cut = pos + size
                has_any_moof = True

        pos += size

    # Walked past all moofs without exceeding target → 完整 buffer 都通過
    # 但「沒任何 moof」時 timescale 無意義 → 回 None（呼叫端 fallback）
    if timescale is not None and has_any_moof:
        return last_safe_cut
    return None


def _truncate_fmp4_chunks(
    chunks,
    target_seconds: Optional[float] = None,
):
    """包 fmp4 chunk iterator：在 cumulative fragment decode time ≥ target 時停止 yield。

    Args:
        chunks: source Iterator[bytes]（fmp4 stream）
        target_seconds: 目標秒數。None → 原樣 yield（向後相容）。

    Yields:
        bytes: fmp4 prefix chunks 直到 stop point

    Algorithm：
        1. 完整 drain source 到 memory buffer（30s/1080p ≈ 15MB，OK）。
        2. Walk fmp4 boxes 找「cumulative decode time ≥ target」的第一個 moof 邊界。
        3. Yield 該邊界之前的所有 bytes。
        4. 若 moov/tfdt 都沒找到（NVR 不送 init segment）→ fallback byte budget：
           target_bytes = target_seconds × 1Mbps / 8（保守中間值）。
    """
    if target_seconds is None:
        yield from chunks
        return

    # 1. Drain into single buffer
    buf = bytearray(b"".join(chunks))
    if not buf:
        return

    # 2. 嘗試 mp4-aware 截斷
    cut = _find_fmp4_cut_point(bytes(buf), target_seconds)

    # 3. Fallback: byte budget
    if cut is None:
        # 1 Mbps 保守估計（覆蓋 NVR 9Mbps 高解析流）
        bytes_budget = int(target_seconds * 1_000_000 / 8)
        cut = min(bytes_budget, len(buf))

    # 4. Yield prefix as one chunk
    if cut > 0:
        yield bytes(buf[:cut])
