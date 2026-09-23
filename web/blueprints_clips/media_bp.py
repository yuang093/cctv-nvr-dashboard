"""Week 6 #018 — clips_media_bp。

URL prefix: `/clips`
路由（5 條）：
    `/clips/nvrs`        GET   NVR dropdown JSON
    `/clips/cameras`     GET   單 NVR cameras JSON
    `/clips/snapshots`   GET   並行抓 snapshots
    `/clips/fetch`       POST  同步單段 clip bytes（含擴搜邏輯）
    `/clips/fetch_sync`  POST  多 cam 同步 multipart

依賴：
- web.clip_retrieval（Protocol + Mpd / Mock + 例外三類）
- web.clips_helpers 全部 helper
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import cast

from flasgger import swag_from

from flask import Blueprint, current_app, jsonify, render_template, request, Response

from web import db as webdb
from web import clips_app as _ch  # lazy attrs via clips_app module (monkeypatch-friendly)
from web.clips_helpers import SessionStore  # class for type hint only; callsites use _ch.X
from web.clip_retrieval import (
    MediaApiClient,
    NvrAuthError,
    NvrInternalError,
    NvrNoRecordingError,
)

logger = logging.getLogger("nvr.clips")
media_bp = Blueprint("clips_media", __name__, url_prefix="/clips")

_NO_STALE_TRUST_TTL = 600  # 10 分鐘


def _session_store() -> SessionStore:
    """從 app.config 拿 SessionStore（測試可注入 mock；生產由 clips_app 設）。"""
    return cast(SessionStore, current_app.config["SESSION_STORE"])


@media_bp.route("/nvrs")
@swag_from("web.openapi.clips.media_nvrs.yml")
def nvrs():
    """JSON：所有 NVR 清單（給 dropdown 用）。"""
    rows = webdb.get_nvrs(_ch.get_db_path())
    return jsonify(
        [
            {
                "internal_id": r["id"],
                "nvr_id": r["nvr_id"],
                "name": r["name"],
                "host": r["host"],
                "camera_count": r.get("camera_count", 0),
            }
            for r in rows
        ]
    )


@media_bp.route("/cameras")
@swag_from("web.openapi.clips.media_cameras.yml")
def cameras():
    """?nvr_id=<internal_id> → JSON cameras list。"""
    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    if not internal_id:
        return jsonify({"error": "缺少 nvr_id"}), 400
    cams = webdb.list_cameras_for_nvr(_ch.get_db_path(), internal_id)
    return jsonify(cams)


@media_bp.route("/snapshots")
@swag_from("web.openapi.clips.media_snapshots.yml")
def snapshots():
    """並行抓 N 台相機的 snapshot，縮圖後 JSON 回傳。

    Query: ?nvr_id=<int>&t=<ISO8601 UTC>&camera_ids=A,B,C（可選過濾）
    """
    try:
        internal_id = int(request.args.get("nvr_id", "0"))
    except ValueError:
        return jsonify({"error": "nvr_id 必須是整數"}), 400
    t_str = request.args.get("t", "")
    if not internal_id or not t_str:
        return jsonify({"error": "缺少 nvr_id 或 t"}), 400

    try:
        if t_str.endswith("Z"):
            t_str = t_str[:-1] + "+00:00"
        at_time = datetime.fromisoformat(t_str)
        if at_time.tzinfo is None:
            at_time = at_time.replace(tzinfo=timezone.utc)
    except ValueError as e:
        return jsonify({"error": f"t 解析失敗：{e}"}), 400

    nvr_row = webdb.get_nvr(_ch.get_db_path(), internal_id)
    if not nvr_row:
        return jsonify({"error": f"找不到 NVR internal_id={internal_id}"}), 404

    cams = webdb.list_cameras_for_nvr(_ch.get_db_path(), internal_id)
    if not cams:
        return jsonify({"warning": "此 NVR 沒有相機", "snapshots": []})

    # 拿 session
    import os

    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock":
        session_token = "MOCK-SESSION"
    else:
        try:
            session_token = _ch.get_session_for_nvr(internal_id, _session_store())
        except Exception as e:
            logger.error("login 失敗：%s", e)
            return jsonify({"error": f"login 失敗：{e}"}), 502

    client = _ch.get_client_for_nvr(nvr_row, session_token=session_token)
    selected = request.args.get("camera_ids", "").strip()
    if selected:
        wanted = set(s.strip() for s in selected.split(",") if s.strip())
        cams = [c for c in cams if c["device_id"] in wanted]
    camera_ids = [c["device_id"] for c in cams]
    id_to_name = {c["device_id"]: c["name"] for c in cams}
    snapshots = _ch.fetch_snapshots_parallel(client, camera_ids, at_time)
    for s in snapshots:
        s["camera_name"] = id_to_name.get(
            s.get("camera_id", ""), s.get("camera_id", "")
        )
    return jsonify(
        {
            "nvr_id": internal_id,
            "t": at_time.isoformat(),
            "camera_count": len(camera_ids),
            "snapshots": snapshots,
        }
    )


@media_bp.route("/fetch", methods=["POST"])
@swag_from("web.openapi.clips.media_fetch.yml")
def fetch_clip():
    """POST {nvr_id, camera_id, start, end} → stream fragmented MP4 bytes。

    擴搜邏輯（2026-07-07）：
    1. 先用原 start 試一次 get_recording_duration
    2. 若 < 1s → 回 404 + NO_RECORDING
    3. 若 < 30s → 自動往外擴（start 往前 / end 往後各 +5s）直到湊到 ≥ 30s
       或擴到邊界為止
    4. 真正的 start / end 透過 X-Actual-Start / X-Actual-End / X-Actual-Duration
       response header 回傳給前端顯示
    """
    import os

    _t_cf = {"mpd": 0.0, "fetch": 0.0}
    _t_cf_total0 = time.monotonic()
    payload = request.get_json(silent=True) or {}
    try:
        internal_id = int(payload.get("nvr_id", 0))
        camera_id = str(payload.get("camera_id", "")).strip()
        start = datetime.fromisoformat(payload["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload["end"].replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"參數錯誤：{e}"}), 400
    if not internal_id or not camera_id:
        return jsonify({"error": "缺少 nvr_id 或 camera_id"}), 400

    nvr_row = webdb.get_nvr(_ch.get_db_path(), internal_id)
    if not nvr_row:
        return jsonify({"error": f"找不到 NVR internal_id={internal_id}"}), 404

    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock":
        session_token = "MOCK-SESSION"
    else:
        try:
            session_token = _ch.get_session_for_nvr(internal_id, _session_store())
        except Exception as e:
            logger.error("login 失敗：%s", e)
            return jsonify({"error": f"login 失敗：{e}"}), 502

    client = _ch.get_client_for_nvr(nvr_row, session_token=session_token)
    target_seconds = (end - start).total_seconds()

    logger.info(
        "[fetch] request: cam=%s nvr=%d req_start=%s req_end=%s target=%.1fs",
        camera_id,
        internal_id,
        start.isoformat(),
        end.isoformat(),
        target_seconds,
    )

    actual_start = start
    actual_end = end
    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() != "mock":
        step = timedelta(seconds=5)
        max_iters = int(target_seconds / 5) + 2
        try:
            for _ in range(max_iters):
                dur = client.get_recording_duration(camera_id, actual_start)
                if dur <= 0.5:
                    break
                coverage_end = actual_start + timedelta(seconds=dur)
                actual_end = coverage_end
                actual_seconds = (actual_end - actual_start).total_seconds()
                if actual_seconds >= target_seconds:
                    break
                actual_start = actual_start - step
        except Exception as e:
            logger.warning("擴搜失敗（繼續用原 start..end）：%s", e)
            actual_start = start
            actual_end = end

    actual_seconds = (actual_end - actual_start).total_seconds()
    logger.info(
        "[fetch] expanded: cam=%s actual_start=%s actual_end=%s actual_seconds=%.2f target=%.1fs",
        camera_id,
        actual_start.isoformat(),
        actual_end.isoformat(),
        actual_seconds,
        target_seconds,
    )
    _t_cf["mpd"] = time.monotonic() - _t_cf_total0
    if actual_seconds <= 0.5:
        return (
            jsonify(
                {
                    "error": "NO_RECORDING",
                    "message": f"此時段無錄影（{start.isoformat()} ~ {end.isoformat()}）",
                    "requested_start": start.isoformat(),
                    "requested_end": end.isoformat(),
                }
            ),
            404,
        )

    filename = f"{nvr_row['nvr_id']}_{camera_id}_{int(actual_start.timestamp())}.mp4"

    try:
        _t_fetch_start = time.monotonic()
        body = b"".join(
            client.fetch_clip(
                camera_id,
                actual_start,
                actual_end,
                max_wall_seconds=_ch._MAX_FETCH_WALL_SECONDS,
            )
        )
        _t_cf["fetch"] = time.monotonic() - _t_fetch_start
        logger.info(
            "[fetch] fetched: cam=%s bytes=%d actual_start=%s actual_end=%s",
            camera_id,
            len(body),
            actual_start.isoformat(),
            actual_end.isoformat(),
        )
    except NvrNoRecordingError as e:
        logger.info("/clips/fetch NO_RECORDING：%s", e)
        return (
            jsonify(
                {
                    "error": "NO_RECORDING",
                    "message": "此時段無錄影資料",
                    "stage": "nvr_404",
                    "nvr_id": nvr_row["nvr_id"],
                    "camera_id": camera_id,
                    "actual_start": actual_start.isoformat(),
                    "actual_end": actual_end.isoformat(),
                }
            ),
            404,
        )
    except NvrAuthError as e:
        logger.warning("/clips/fetch AUTH_FAILED：%s", e)
        return (
            jsonify(
                {
                    "error": "AUTH_FAILED",
                    "message": "NVR 認證失敗（session 過期）",
                    "detail": str(e),
                    "stage": "nvr_auth",
                    "nvr_id": nvr_row["nvr_id"],
                    "camera_id": camera_id,
                }
            ),
            502,
        )
    except NvrInternalError as e:
        logger.error("/clips/fetch NVR_INTERNAL_ERROR：%s", e)
        return (
            jsonify(
                {
                    "error": "NVR_INTERNAL_ERROR",
                    "message": "NVR 內部錯誤",
                    "detail": str(e),
                    "stage": "fetch_clip",
                    "nvr_id": nvr_row["nvr_id"],
                    "camera_id": camera_id,
                    "actual_start": actual_start.isoformat(),
                    "actual_end": actual_end.isoformat(),
                }
            ),
            502,
        )
    except RuntimeError as e:
        logger.error("/clips/fetch NVR_INTERNAL_ERROR (legacy RuntimeError)：%s", e)
        return (
            jsonify(
                {
                    "error": "NVR_INTERNAL_ERROR",
                    "message": "NVR 內部錯誤",
                    "detail": str(e),
                    "stage": "fetch_clip_legacy",
                    "nvr_id": nvr_row["nvr_id"],
                    "camera_id": camera_id,
                }
            ),
            502,
        )
    except Exception as e:
        logger.exception("/clips/fetch 未預期錯誤")
        return (
            jsonify(
                {
                    "error": "SERVER_ERROR",
                    "message": f"伺服器錯誤：{type(e).__name__}: {e}",
                    "stage": "fetch_clip",
                }
            ),
            500,
        )

    if not body:
        return (
            jsonify(
                {
                    "error": "EMPTY_CLIP",
                    "message": "此時段無錄影資料",
                    "stage": "empty_body",
                    "actual_start": actual_start.isoformat(),
                    "actual_end": actual_end.isoformat(),
                }
            ),
            404,
        )

    return Response(
        body,
        mimetype="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Camera-Id": camera_id,
            "X-Nvr-Id": str(internal_id),
            "X-Actual-Start": actual_start.isoformat(),
            "X-Actual-End": actual_end.isoformat(),
            "X-Actual-Duration": f"{actual_seconds:.2f}",
            "X-Requested-Duration": f"{target_seconds:.2f}",
            "X-Truncated": "true" if actual_seconds < target_seconds else "false",
            "Content-Length": str(len(body)),
            "X-Server-Timing": ", ".join(
                f"{phase};dur={t * 1000:.1f}" for phase, t in _t_cf.items()
            )
            + f", total;dur={(time.monotonic() - _t_cf_total0) * 1000:.1f}",
        },
    )


@media_bp.route("/fetch_sync", methods=["POST"])
@swag_from("web.openapi.clips.media_fetch_sync.yml")
def fetch_sync():
    """2 台以上 cam 同時撥放：算交集區間 → 回 4 段 multipart/mixed。

    Body: {nvr_id, cameras: [{device_id, name}, ...], t_center: ISO8601,
           target_seconds: int=60}
    """
    import os

    _t_phase = {"mpd": 0.0, "probe": 0.0, "fetch": 0.0}
    _t_total0 = time.monotonic()

    payload = request.get_json(silent=True) or {}
    try:
        internal_id = int(payload.get("nvr_id", 0))
        cameras = payload.get("cameras") or []
        t_center_iso = payload["t_center"]
        target_seconds = int(payload.get("target_seconds", 60))
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"參數錯誤：{e}"}), 400
    if not internal_id or not cameras or len(cameras) < 1:
        return jsonify({"error": "缺少 nvr_id 或 cameras"}), 400

    t_center = datetime.fromisoformat(t_center_iso.replace("Z", "+00:00"))
    half_window = timedelta(seconds=target_seconds / 2)
    request_start = t_center - half_window
    request_end = t_center + half_window

    nvr_row = webdb.get_nvr(_ch.get_db_path(), internal_id)
    if not nvr_row:
        return jsonify({"error": f"找不到 NVR internal_id={internal_id}"}), 404

    if os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock":
        session_token = "MOCK-SESSION"
    else:
        try:
            session_token = _ch.get_session_for_nvr(internal_id, _session_store())
        except Exception as e:
            logger.error("login 失敗：%s", e)
            return jsonify({"error": f"login 失敗：{e}"}), 502

    client = _ch.get_client_for_nvr(nvr_row, session_token=session_token)
    is_mock = os.environ.get("NVR_CLIPS_CLIENT", "").lower() == "mock"

    def query_cam_availability(cam_spec):
        cam_id = cam_spec.get("device_id", "")
        cam_name = cam_spec.get("name", cam_id)
        try:
            dur = client.get_recording_duration(cam_id, request_start)
            return {
                "camera_id": cam_id,
                "name": cam_name,
                "available_start": request_start,
                "available_end": request_start + timedelta(seconds=dur)
                if dur > 0
                else request_start,
                "duration": dur,
                "error": None,
                "auth_failed": False,
            }
        except NvrAuthError as e:
            return {
                "camera_id": cam_id,
                "name": cam_name,
                "available_start": request_start,
                "available_end": request_start,
                "duration": 0,
                "error": str(e),
                "auth_failed": True,
            }
        except Exception as e:
            return {
                "camera_id": cam_id,
                "name": cam_name,
                "available_start": request_start,
                "available_end": request_start,
                "duration": 0,
                "error": str(e),
                "auth_failed": False,
            }

    with ThreadPoolExecutor(max_workers=min(8, len(cameras))) as ex:
        _t_mpd_start = time.monotonic()
        cam_results = list(ex.map(query_cam_availability, cameras))
    _t_phase["mpd"] = time.monotonic() - _t_mpd_start

    active_cams = [c for c in cam_results if c["duration"] > 0]
    if not active_cams:
        if cam_results and all(c.get("auth_failed") for c in cam_results):
            current_app.config["SESSION_STORE"].clear(internal_id)
            try:
                session_token = _ch.get_session_for_nvr(internal_id, _session_store())
            except Exception as e:
                logger.error("[fetch_sync] retry login 失敗：%s", e)
                return jsonify({"error": f"retry login 失敗：{e}"}), 502
            client = _ch.get_client_for_nvr(nvr_row, session_token=session_token)

            def query_cam_availability_fresh(cam_spec):
                cam_id = cam_spec.get("device_id", "")
                cam_name = cam_spec.get("name", cam_id)
                try:
                    dur = client.get_recording_duration(cam_id, request_start)
                    return {
                        "camera_id": cam_id,
                        "name": cam_name,
                        "available_start": request_start,
                        "available_end": request_start + timedelta(seconds=dur)
                        if dur > 0
                        else request_start,
                        "duration": dur,
                        "error": None,
                        "auth_failed": False,
                    }
                except NvrAuthError as e:
                    return {
                        "camera_id": cam_id,
                        "name": cam_name,
                        "available_start": request_start,
                        "available_end": request_start,
                        "duration": 0,
                        "error": str(e),
                        "auth_failed": True,
                    }
                except Exception as e:
                    return {
                        "camera_id": cam_id,
                        "name": cam_name,
                        "available_start": request_start,
                        "available_end": request_start,
                        "duration": 0,
                        "error": str(e),
                        "auth_failed": False,
                    }

            with ThreadPoolExecutor(max_workers=min(8, len(cameras))) as ex2:
                cam_results = list(
                    ex2.map(query_cam_availability_fresh, cameras)
                )
            active_cams = [c for c in cam_results if c["duration"] > 0]
        if not active_cams:
            return (
                jsonify(
                    {
                        "error": "NO_COMMON_RECORDING",
                        "message": "無可用的錄影時段（所有 cam 都查無資料）",
                        "stage": "no_cam_available",
                        "intersection_start": request_start.isoformat(),
                        "intersection_end": request_end.isoformat(),
                    }
                ),
                404,
            )

    # Stale probe（預設關閉）
    stale_cam_ids: set[str] = set()
    skip_stale_probe = is_mock or getattr(client, "disable_stale_probe", False)
    if not skip_stale_probe:
        _probe_enabled = str(payload.get("probe", "")).lower() in (
            "1",
            "true",
            "yes",
        )
        if not _probe_enabled:
            skip_stale_probe = True
    if not skip_stale_probe:
        _trust_dict = current_app.config.setdefault("NO_STALE_TRUST", {})
        _trust_expire = _trust_dict.get(internal_id, 0)
        if _trust_expire > time.time():
            skip_stale_probe = True
    if not skip_stale_probe:
        _t_probe_start = time.monotonic()
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _run_probe(cam_info):
            try:
                is_stale, evidence = _ch.probe_nvr_stale_cache(
                    client,
                    cam_info["camera_id"],
                    request_start,
                )
                return cam_info["camera_id"], is_stale, evidence
            except Exception as e:
                logger.warning(
                    "[fetch_sync] stale probe exception: cam=%s err=%s",
                    cam_info["camera_id"],
                    e,
                )
                return cam_info["camera_id"], False, []

        with _TPE(max_workers=min(8, len(active_cams))) as ex:
            for cid, is_stale, evidence in ex.map(_run_probe, active_cams):
                if is_stale:
                    stale_cam_ids.add(cid)
        if not stale_cam_ids:
            _trust_dict = current_app.config.setdefault("NO_STALE_TRUST", {})
            _trust_dict[internal_id] = time.time() + _NO_STALE_TRUST_TTL
    if stale_cam_ids:
        excluded = []
        for c in active_cams:
            if c["camera_id"] in stale_cam_ids:
                c["duration"] = 0
                c["error"] = "NVR_STALE_CACHE"
                excluded.append(
                    {
                        "camera_id": c["camera_id"],
                        "name": c["name"],
                        "reason": "NVR_STALE_CACHE",
                    }
                )
        active_cams = [c for c in active_cams if c["duration"] > 0]
        if not active_cams:
            return (
                jsonify(
                    {
                        "error": "NO_COMMON_RECORDING",
                        "message": "所有 cam 的 NVR fmp4 都回 stale bytes（多時段相同），可能是 NVR Media API 異常",
                        "stage": "all_cams_stale",
                        "intersection_start": request_start.isoformat(),
                        "intersection_end": request_end.isoformat(),
                        "excluded_cams": excluded,
                    }
                ),
                502,
            )

    intersection_start = request_start
    intersection_length = float(target_seconds)
    intersection_end = request_start + timedelta(seconds=intersection_length)

    if intersection_length < 5.0:
        cam_ranges = [
            {
                "camera_id": c["camera_id"],
                "name": c["name"],
                "available_start": c["available_start"].isoformat(),
                "available_end": c["available_end"].isoformat(),
                "duration_sec": c["duration"],
                "error": c["error"],
            }
            for c in cam_results
        ]
        return (
            jsonify(
                {
                    "error": "NO_COMMON_RECORDING",
                    "message": f"無共同錄影時段（交集只 {intersection_length:.1f} 秒，建議換時段）",
                    "stage": "intersection_too_short",
                    "intersection_length_sec": intersection_length,
                    "intersection_start": intersection_start.isoformat(),
                    "intersection_end": intersection_end.isoformat(),
                    "cam_ranges": cam_ranges,
                }
            ),
            404,
        )

    boundary = f"----NVRCLIPSYNC{uuid.uuid4().hex[:12]}"

    def fetch_one_cam(cam_info):
        slot_idx = cam_info["slot_idx"]
        cam_id = cam_info["camera_id"]
        cam_name = cam_info["name"]
        try:
            body = b"".join(
                client.fetch_clip(
                    cam_id,
                    intersection_start,
                    intersection_end,
                    target_seconds=intersection_length,
                    max_wall_seconds=_ch._MAX_FETCH_WALL_SECONDS,
                )
            )
            if not body:
                return (
                    slot_idx,
                    {
                        "slot_id": slot_idx,
                        "camera_id": cam_id,
                        "name": cam_name,
                        "error": "EMPTY_CLIP",
                    },
                    None,
                )
            return (
                slot_idx,
                {
                    "slot_id": slot_idx,
                    "camera_id": cam_id,
                    "name": cam_name,
                    "actual_start": intersection_start.isoformat(),
                    "actual_end": intersection_end.isoformat(),
                    "actual_duration": f"{intersection_length:.2f}",
                    "intersection_truncated": cam_info["duration"]
                    > intersection_length,
                    "cam_available_duration": f"{cam_info['duration']:.2f}",
                },
                body,
            )
        except Exception as e:
            return (
                slot_idx,
                {
                    "slot_id": slot_idx,
                    "camera_id": cam_id,
                    "name": cam_name,
                    "error": str(e),
                },
                None,
            )

    _t_fetch_start = time.monotonic()
    cam_with_idx = [{**r, "slot_idx": i} for i, r in enumerate(active_cams)]
    with ThreadPoolExecutor(max_workers=min(8, len(cam_with_idx))) as ex:
        futures = [ex.submit(fetch_one_cam, c) for c in cam_with_idx]
        fetch_results = [f.result() for f in futures]
    _t_phase["fetch"] = time.monotonic() - _t_fetch_start

    _t_total = time.monotonic() - _t_total0

    def generate_multipart():
        crlf = b"\r\n"
        for slot_idx, meta, body in fetch_results:
            yield b"--" + boundary.encode("ascii") + crlf
            yield b"X-Slot-Id: " + str(slot_idx).encode("ascii") + crlf
            yield b"X-Camera-Id: " + meta["camera_id"].encode("utf-8") + crlf
            yield (
                b"X-Camera-Name: "
                + meta.get("name", meta["camera_id"]).encode("utf-8")
                + crlf
            )
            if "error" in meta and meta["error"]:
                yield b"X-Slot-Error: " + meta["error"][:200].encode("utf-8") + crlf
                yield b"Content-Length: 0" + crlf
                yield b"Content-Type: video/mp4" + crlf
                yield crlf
                yield crlf
            else:
                yield b"X-Actual-Start: " + meta["actual_start"].encode("ascii") + crlf
                yield b"X-Actual-End: " + meta["actual_end"].encode("ascii") + crlf
                yield (
                    b"X-Actual-Duration: "
                    + meta["actual_duration"].encode("ascii")
                    + crlf
                )
                yield (
                    b"X-Cam-Available-Duration: "
                    + meta.get("cam_available_duration", "0").encode("ascii")
                    + crlf
                )
                yield b"Content-Type: video/mp4" + crlf
                yield crlf
                yield body
                yield crlf
        yield b"--" + boundary.encode("ascii") + b"--" + crlf

    return Response(
        generate_multipart(),
        mimetype=f"multipart/mixed; boundary={boundary}",
        headers={
            "X-Intersection-Start": intersection_start.isoformat(),
            "X-Intersection-End": intersection_end.isoformat(),
            "X-Intersection-Length": f"{intersection_length:.2f}",
            "X-Cam-Count": str(len(cameras)),
            "X-Excluded-Cams": _ch.format_excluded_cams(stale_cam_ids, list(cam_results)),
            "X-Server-Timing": ", ".join(
                f"{phase};dur={t * 1000:.1f}" for phase, t in _t_phase.items()
            )
            + f", total;dur={_t_total * 1000:.1f}",
        },
    )
