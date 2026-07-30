"""
batch_scan.py
=============
多 NVR 批次掃描協調器（依 class_interface.md 契約）。

職責：
    - 從 nvr_config.json 讀 enabled=True 的 NVR 清單
    - 對每台 NVR 建立獨立 AvigilonScanner instance（不共用 HTTP session）
    - per-NVR try/except：失敗不中斷整批，記錄到 failures 清單
    - 一個 batch = 一個 scan_run（單一 transaction，commit 在 finish 時）
    - 印出彙總報表
    - 異常時透過 webhook 推送（webhook.py）

設計依據：
    - class_interface.md §「批次入口：batch_scan()」
    - database_schema.md §3 scan_runs（status: running / success / partial / failed）
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from db.sqlite_writer import SqliteWriter
from nvr_scanner import (
    DEFAULT_TIMEOUT,
    AvigilonScanner,
    ScannerError,
    print_report,
)
from webhook import (
    WebhookConfig,
    WebhookPayload,
    send_webhooks,
)

from web.image_health import (
    analyze_image,
    is_frozen,
)


def _now_utc_iso() -> str:
    """當下 UTC 時間，ISO 8601 格式。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# === Phase 2.8（Arisan 影像健康巡檢）：batch_scan 內 image_health stage ===
# 預設：跳過（避免初次啟動拖累 scan 速度）；用 NVR_IMAGE_HEALTH=1 環境變數開啟。
import os as _os_image_health

# 2026-07-30 修 /wall 縮圖 bug：預設開啟。
# /wall 縮圖依賴 image_health_loop 內的 upsert_snapshot；
# 預設關 → Web UI /scan 永遠不寫 snapshot → /wall 永遠顯示 placeholder。
# 設 NVR_IMAGE_HEALTH=0 可 opt-out（NVR 暫時不穩時避免拖累 scan）。
_IMAGE_HEALTH_ENABLED = _os_image_health.environ.get("NVR_IMAGE_HEALTH", "1") != "0"
_FROZEN_INTERVAL_SEC = 5  # 兩張 jpeg 間隔（frozen 偵測用）

# === Phase 2.8（Arisan 錄影完整率）：24h /timeline 取得 + 計算完整率 寫 DB ===
# 預設：跳過；用 NVR_TIMELINE=1 啟用。
import os as _os_timeline

_TIMELINE_ENABLED = _os_timeline.environ.get("NVR_TIMELINE", "0") == "1"
_TIMELINE_WINDOW_HOURS = 24  # 預設看 24h


def _summarize_timeline(
    parsed: dict[str, list[tuple]],
    window_start,
    window_end,
) -> list[dict]:
    """從 `web.timeline.parse_timeline_response` 結果算出每台 cam 的 (completeness, missing_seconds)。

    Returns:
        list of {"camera_id", "completeness", "missing_seconds"}；沒資料的 cam 不會出現。
    """
    from web.timeline import compute_completeness, compute_missing_segments

    out: list[dict] = []
    for cam_id, records in parsed.items():
        completeness = compute_completeness(records, window_start, window_end)
        gaps = compute_missing_segments(records, window_start, window_end)
        missing_seconds = sum(
            (g[1] - g[0]).total_seconds() for g in gaps
        )
        out.append({
            "camera_id": cam_id,
            "completeness": completeness,
            "missing_seconds": missing_seconds,
        })
    return out


def _timeline_check_loop(
    scanner: "AvigilonScanner",
    nvr_int_id: int,
    writer: "SqliteWriter",
    *,
    window_hours: int = _TIMELINE_WINDOW_HOURS,
    verbose: bool = False,
) -> dict:
    """Phase 2.8（Arisan）：從 NVR 每台 cam 抓 24h /timeline，寫 recording_status。

    Returns:
        {"checked": int, "written": int, "errors": list[str]}
    """
    from web.timeline import parse_timeline_response

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=window_hours)
    from_iso = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso = window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    ws = window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    we = window_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 從 DB 拿本次掃描所有 cam 的 device_id
    cameras = writer._get_conn().execute(
        "SELECT device_id FROM cameras WHERE nvr_id=?", (nvr_int_id,),
    ).fetchall()

    checked = 0
    written = 0
    errors: list[str] = []
    for row in cameras:
        cam_id = row["device_id"]
        checked += 1
        try:
            raw = scanner.get_timeline(cam_id, from_iso=from_iso, to_iso=to_iso)
            parsed = parse_timeline_response(raw)
            summaries = _summarize_timeline(parsed, window_start, window_end)
            for s in summaries:
                writer.upsert_recording_status(
                    nvr_int_id, s["camera_id"],
                    window_start=ws,
                    window_end=we,
                    completeness=s["completeness"],
                    missing_seconds=s["missing_seconds"],
                )
                written += 1
        except Exception as exc:
            errors.append(f"{cam_id}: {type(exc).__name__}: {exc}")
            if verbose:
                print(f"  [timeline] {cam_id} 失敗：{exc}", file=sys.stderr)
    return {"checked": checked, "written": written, "errors": errors}


def _image_health_check_loop(
    scanner: AvigilonScanner,
    nvr_int_id: int,
    run_id: int,
    writer: SqliteWriter,
    *,
    verbose: bool = False,
    frozen_interval_sec: float | None = None,
) -> dict:
    """對該 NVR 所有 cam 抓 2 張 jpeg → analyze_image + is_frozen → 寫 image_health_checks。

    Args:
        scanner: 已 login 過的 AvigilonScanner（用既有 session_token 抓 jpeg）
        nvr_int_id: nvr_servers.id（給 image_health_checks.nvr_server_id）
        run_id: 當次 scan_run_id（未使用，預留給未來關聯 metrics→run）
        writer: SqliteWriter（必須在 active transaction 內）
        verbose: True 時 print 每台 cam 結果
        frozen_interval_sec: 兩張 jpeg 間隔秒數（測試用 None=跳過 sleep）

    Returns:
        {
            "checked": int,         # 跑過 image_health 的 cam 數
            "triggered": int,       # 觸發新 event 的 cam 數
            "errors": list[str],    # 個別 cam 失敗訊息（不中斷整批）
        }
    """
    if frozen_interval_sec is None:
        frozen_interval_sec = _FROZEN_INTERVAL_SEC
    summary = {"checked": 0, "triggered": 0, "errors": []}
    # 取得 cam 清單（從 scanner 內部已抓過的 _cameras 或重新 GET；這裡直接呼叫 get_cameras）
    try:
        cams = scanner.get_cameras()
    except Exception as e:
        summary["errors"].append(f"get_cameras 失敗：{e}")
        return summary

    for device_id, info in cams.items():
        # 跳過無連線的 cam（抓 jpeg 一定失敗）
        if info.get("connection_state") != "CONNECTED":
            continue
        try:
            # 2026-07-30：verbose 版回傳 (bytes, error_msg)，方便未來診斷
            jpeg_a, err_a = scanner.fetch_thumbnail_with_status(device_id)
            if not jpeg_a:
                summary["errors"].append(
                    f"{device_id}: 第一張 jpeg 抓不到（{err_a or 'unknown'}）"
                )
                continue

            # 2026-07-29（Wall 縮圖重構）：用第一張 jpeg 順便存縮圖快取
            # 一次 HTTPS 同時服務 image_health + /wall 縮圖，省一次 API 呼叫。
            # 縮圖失敗（Pillow 缺 / 壞 bytes）→ fallback 原 bytes，仍會 upsert_snapshot
            # 寫入失敗 → 跳過、不影響 image_health 主流程
            try:
                from web.snapshot import compress_to_thumbnail, get_thumbnail_dimensions
                thumb = compress_to_thumbnail(jpeg_a)
                try:
                    w_, h_ = get_thumbnail_dimensions(thumb)
                except Exception:
                    w_, h_ = (160, 120)  # fallback 預設
                writer.upsert_snapshot(
                    nvr_int_id, device_id,
                    jpeg_bytes=thumb, width=w_, height=h_,
                )
            except Exception as snap_exc:
                summary["errors"].append(
                    f"{device_id}: snapshot 寫入失敗：{type(snap_exc).__name__}: {snap_exc}"
                )

            time.sleep(frozen_interval_sec)
            jpeg_b, err_b = scanner.fetch_thumbnail_with_status(device_id)
            if not jpeg_b:
                summary["errors"].append(
                    f"{device_id}: 第二張 jpeg 抓不到（{err_b or 'unknown'}）"
                )
                continue

            # 分析單張
            r = analyze_image(jpeg_a)
            metrics = r.to_metrics_dict()
            flags = r.to_flags()
            # 凍結比對
            fr = is_frozen(jpeg_a, jpeg_b)
            metrics["frozen_diff"] = round(fr.mean_abs_diff, 3)
            if fr.is_frozen:
                flags.append("frozen")
            metrics["is_frozen"] = fr.is_frozen

            checked_at = _now_utc_iso()
            metrics_str = json.dumps(metrics)
            flags_str = json.dumps(flags)
            # inline SQL（直接寫，繞過 SqliteWriter method 行為差異）
            conn_h = writer._require_active()
            cur = conn_h.execute(
                "INSERT INTO image_health_checks "
                "(camera_id, nvr_server_id, checked_at_utc, metrics_json, flags_json, triggered_event_ids) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, nvr_int_id, checked_at, metrics_str, flags_str, "[]"),
            )
            new_id = cur.lastrowid
            # 反查 cameras.id → 更新 last_health_check_id
            # 注：cameras 表用 nvr_id 欄位（非 nvr_server_id）
            cam_row = conn_h.execute(
                "SELECT id FROM cameras WHERE nvr_id=? AND device_id=?",
                (nvr_int_id, device_id),
            ).fetchone()
            if cam_row:
                conn_h.execute(
                    "UPDATE cameras SET last_health_check_id=? WHERE id=?",
                    (new_id, cam_row[0]),
                )
            summary["checked"] += 1

            # 若有新觸發的 flag → 寫 events 表（source 記在 raw_json）
            # 2026-07-30：frozen 不再觸 event（cam2 明亮低動態場景持續誤報 80%）。
            # 仍寫 metrics（flags_json 內含 frozen / metrics_json 內 is_frozen=True）
            # 供未來 SQL 查，但 events 表不再增加 false positive。
            # 真凍結偵測待未來 is_frozen 演算法升級（feature matching / SSIM）。
            triggering_flags = [f for f in flags if f != "frozen"]
            if triggering_flags:
                # 觸發就寫一條 event（kind=IMAGE_HEALTH，topic=第一個非 frozen flag）
                from uuid import uuid4
                primary_topic = "IMAGE_HEALTH_" + (
                    "BLURRY" if r.is_blurry else
                    "OVEREXPOSED" if r.is_overexposed else
                    "UNDEREXPOSED" if r.is_underexposed else
                    "ANOMALY"
                )
                ev = {
                    "eventId": f"img-health-{uuid4().hex[:12]}",
                    "deviceId": device_id,
                    "eventTopics": [primary_topic] + [
                        f for f in triggering_flags if f != primary_topic.split("IMAGE_HEALTH_")[-1].lower()
                    ],
                    "eventTopic": primary_topic,
                    "occurred_at": checked_at,
                    "source": "image_health",
                    "metrics": metrics,
                    "flags": triggering_flags,  # event 的 flags 只列會觸發的，不含 frozen
                    "image_health_check_id": new_id,
                }
                writer.insert_events(run_id, nvr_int_id, [ev])
                # 回填 triggered_event_ids
                writer._require_active().execute(
                    "UPDATE image_health_checks SET triggered_event_ids=? WHERE id=?",
                    (json.dumps([ev["eventId"]]), new_id),
                )
                summary["triggered"] += 1

            if verbose:
                flag_str = ",".join(flags) if flags else "OK"
                print(f"  [image-health] {device_id}: {flag_str}")
        except Exception as e:
            # 單台 cam 失敗不中斷
            summary["errors"].append(f"{device_id}: {type(e).__name__}: {e}")
    return summary


def _parse_webhook_configs(raw_list: list[dict] | None) -> list[WebhookConfig]:
    """
    從 nvr_config.json 的 webhooks 段建立 WebhookConfig list。

    規則：
        - 缺少 'provider' 或 'url' 的項目跳過（無訊息可送）
        - provider 必須是 'slack' 或 'teams'
        - URL 內的 ${ENV_VAR} 由 webhook.send_webhook() 執行時替換

    Args:
        raw_list: config['webhooks'] 原始 list（可能 None）

    Returns:
        過濾後的 WebhookConfig list（保留順序）。
    """
    if not raw_list:
        return []
    valid: list[WebhookConfig] = []
    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            continue
        provider = item.get("provider")
        url = item.get("url")
        if provider not in ("slack", "teams"):
            print(
                f"[WARN] webhooks[{i}] provider 不支援：{provider}（跳過）",
                file=sys.stderr,
            )
            continue
        if not url:
            print(f"[WARN] webhooks[{i}] url 為空（跳過）", file=sys.stderr)
            continue
        valid.append(WebhookConfig(
            provider=provider,
            url=url,
            channel=item.get("channel"),
            enabled=item.get("enabled", True),
        ))
    return valid


def batch_scan(
    config: dict,
    credentials: dict,
    writer: SqliteWriter,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    verbose: bool = True,
) -> dict:
    """
    批次掃描 config['nvr_servers'] 中的所有 NVR。

    失敗的 NVR 不中斷整批；彙總後：
        - 全部成功 → status='success'
        - 部分成功 → status='partial'
        - 全部失敗 → status='failed'

    Args:
        config: _load_config() 回傳的 dict，含 'nvr_servers' 清單（已過濾 enabled）。
        credentials: {
            'user_nonce': str,
            'user_key': str,
            'integration_id': str,
            'username_override': str | None,   # 環境變數覆寫（無則 None）
            'password_override': str | None,
        }
        writer: SqliteWriter instance（schema 已初始化）。
        timeout: HTTP 逾時秒數（覆寫 AvigilonScanner 預設）。
        verbose: True 時印出每台 NVR 的終端機報表 + 批次彙總。

    Returns:
        {
            'scan_run_id': int,
            'total_nvrs': int,
            'ok_nvrs': int,
            'failed_nvrs': int,
            'total_cameras': int,
            'abnormal_cameras': int,
            'status': str,           # 'success' / 'partial' / 'failed'
            'started_at': str,
            'finished_at': str,
            'per_nvr_results': [
                {'nvr_id': str, 'result': AvigilonScanner.scan() 結構},
                ...
            ],
            'failures': [
                {'nvr_id': str, 'nvr_name': str,
                 'type': str, 'error': str},
                ...
            ],
        }

    Raises:
        ScannerError: config['nvr_servers'] 為空；credentials 缺欄位。
    """
    nvrs = config.get("nvr_servers", [])
    if not nvrs:
        raise ScannerError("config['nvr_servers'] 為空")

    # --- 1. 準備 NVR 清單：套用環境變數帳密覆寫 ---
    user_override = credentials.get("username_override")
    pass_override = credentials.get("password_override")
    prepared: list[dict] = []
    for nvr in nvrs:
        nvr2 = dict(nvr)  # 複製避免汙染原 dict
        if user_override:
            nvr2["username"] = user_override
        if pass_override:
            nvr2["password"] = pass_override
        prepared.append(nvr2)

    # --- 2. 預先 upsert 所有 NVR 到 nvr_servers 表（即使 scan 失敗設定也保留） ---
    nvr_int_ids: dict[str, int] = {}
    for nvr in prepared:
        try:
            nvr_int_ids[nvr["id"]] = writer.upsert_nvr(nvr)
        except Exception as exc:
            # upsert 失敗（理論上不會發生，除非 DB 損壞）→ 該 NVR 無法記錄 events
            print(
                f"[WARN] upsert_nvr 失敗：{nvr.get('id')}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    # --- 3. 開始 batch scan_run（單一 transaction） ---
    started_at = _now_utc_iso()
    run_id = writer.begin_scan_run(started_at)
    if verbose:
        print(
            f"[INFO] batch scan_run_id = {run_id}（共 {len(prepared)} 台 NVR）"
        )

    # --- 4. 逐一掃描 ---
    per_nvr_results: list[dict] = []
    failures: list[dict] = []
    for nvr in prepared:
        nvr_id = nvr.get("id", "?")
        nvr_name = nvr.get("name", "?")
        nvr_int_id = nvr_int_ids.get(nvr_id)
        if verbose:
            print()
            print("=" * 60)
            print(
                f"[INFO] 掃描 {nvr_name} ({nvr_id}) @ "
                f"https://{nvr['host']}:{nvr.get('port', 8443)}"
            )
            print("=" * 60)

        if nvr_int_id is None:
            # upsert 失敗時跳過（仍記為 failure）
            err = ScannerError(
                f"NVR {nvr_id} upsert 失敗，無法記錄 events"
            )
            failures.append({
                "nvr_id": nvr_id,
                "nvr_name": nvr_name,
                "type": type(err).__name__,
                "error": str(err),
            })
            # 寫 nvr_failure_log（upsert 失敗時 nvr_internal_id=None）
            try:
                writer.log_nvr_failure(
                    run_id, nvr_id, nvr_name,
                    error_type=type(err).__name__,
                    error_message=str(err),
                    nvr_internal_id=None,
                )
            except Exception as log_exc:
                print(f"[WARN] log_nvr_failure 失敗：{log_exc}", file=sys.stderr)
            print(
                f"[FAIL] {nvr_id}：{type(err).__name__}: {err}",
                file=sys.stderr,
            )
            continue

        try:
            scanner = AvigilonScanner(
                nvr,
                user_nonce=credentials["user_nonce"],
                user_key=credentials["user_key"],
                integration_id=credentials.get("integration_id", ""),
                timeout=timeout,
            )
            result = scanner.scan()
            # 寫入 cameras + events（仍在 transaction 內）
            writer.upsert_cameras(nvr_int_id, result["cameras"])
            # 2026-07-30：把這次 scan 沒看到的 cam 標 ghost（/wall 不顯示）
            writer.mark_ghost_cameras(nvr_int_id, list(result["cameras"].keys()))
            writer.insert_events(run_id, nvr_int_id, result["events"])
            # Phase 1：把「之前掃描到異常但這次沒再出現」的 device 標記 resolved
            # （失敗的 NVR 不做 — 我們不知道目前真實狀態）
            writer.mark_resolved(run_id, nvr_int_id)
            # Phase 2.8（Arisan 影像健康巡檢）：可選 stage，預設關閉
            # 開啟：NVR_IMAGE_HEALTH=1 python nvr_scanner.py
            image_health_summary = {"checked": 0, "triggered": 0, "errors": []}
            if _IMAGE_HEALTH_ENABLED:
                try:
                    image_health_summary = _image_health_check_loop(
                        scanner, nvr_int_id, run_id, writer, verbose=verbose,
                    )
                    if verbose:
                        print(
                            f"  [image-health] {image_health_summary['checked']} 台 cam 檢查、"
                            f"{image_health_summary['triggered']} 台觸發新事件"
                        )
                except Exception as ih_exc:
                    # image_health stage 失敗不影響主 scan_run
                    print(
                        f"[WARN] image_health stage 失敗：{type(ih_exc).__name__}: {ih_exc}",
                        file=sys.stderr,
                    )
            # Phase 2.8（Arisan 錄影完整率）：可選 stage，預設關閉
            # 開啟：NVR_TIMELINE=1 python nvr_scanner.py
            if _TIMELINE_ENABLED:
                try:
                    timeline_summary = _timeline_check_loop(
                        scanner, nvr_int_id, writer, verbose=verbose,
                    )
                    if verbose:
                        print(
                            f"  [timeline] {timeline_summary['checked']} 台 cam、"
                            f"{timeline_summary['written']} 筆完整率寫入"
                        )
                except Exception as tl_exc:
                    print(
                        f"[WARN] timeline stage 失敗：{type(tl_exc).__name__}: {tl_exc}",
                        file=sys.stderr,
                    )
            per_nvr_results.append({"nvr_id": nvr_id, "result": result})
            if verbose:
                print_report(result)
        except Exception as exc:
            err_type = type(exc).__name__
            err_msg = str(exc)
            failures.append({
                "nvr_id": nvr_id,
                "nvr_name": nvr_name,
                "type": err_type,
                "error": err_msg,
            })
            # 寫 nvr_failure_log（給 dashboard / run_detail 個別顯示）
            try:
                writer.log_nvr_failure(
                    run_id, nvr_id, nvr_name,
                    error_type=err_type,
                    error_message=err_msg,
                    nvr_internal_id=nvr_int_id,
                )
            except Exception as log_exc:
                print(f"[WARN] log_nvr_failure 失敗：{log_exc}", file=sys.stderr)
            print(
                f"[FAIL] {nvr_id} 掃描失敗：{err_type}: {exc}",
                file=sys.stderr,
            )
            # 繼續下一台，不中斷整批

    # --- 5. 彙總 stats + 結束 scan_run（commit transaction） ---
    total_cameras = sum(
        r["result"]["stats"]["total_cameras"] for r in per_nvr_results
    )
    abnormal_cameras = sum(
        r["result"]["stats"]["abnormal_cameras"] for r in per_nvr_results
    )
    ok_count = len(per_nvr_results)
    failed_count = len(failures)

    if ok_count == 0:
        status = "failed"
    elif failed_count > 0:
        status = "partial"
    else:
        status = "success"

    finished_at = _now_utc_iso()
    writer.finish_scan_run(
        run_id,
        finished_at=finished_at,
        status=status,
        stats={
            "total_cameras": total_cameras,
            "abnormal_cameras": abnormal_cameras,
            "total_nvrs": len(prepared),
            "ok_nvrs": ok_count,
            "failed_nvrs": failed_count,
        },
    )

    # --- 6. Webhook 推播（只在有異常時送出；失敗不中斷 worker） ---
    webhook_configs = _parse_webhook_configs(config.get("webhooks"))
    if webhook_configs and (abnormal_cameras > 0 or status == "failed"):
        payload = WebhookPayload(
            run_status=status,
            total_nvrs=len(prepared),
            ok_nvrs=ok_count,
            failed_nvrs=failed_count,
            total_cameras=total_cameras,
            abnormal_cameras=abnormal_cameras,
            started_at=started_at,
            finished_at=finished_at,
            per_nvr_results=per_nvr_results,
        )
        webhook_results = send_webhooks(
            webhook_configs, payload, verbose=verbose,
        )
        # 統計 webhook 結果（加進 batch_result，不丟例外）
        webhook_summary = [
            {"provider": cfg.provider, "success": ok, "error": err}
            for cfg, ok, err in webhook_results
        ]
    else:
        webhook_summary = []

    batch_result = {
        "scan_run_id": run_id,
        "total_nvrs": len(prepared),
        "ok_nvrs": ok_count,
        "failed_nvrs": failed_count,
        "total_cameras": total_cameras,
        "abnormal_cameras": abnormal_cameras,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "per_nvr_results": per_nvr_results,
        "failures": failures,
        "webhook_results": webhook_summary,
    }

    if verbose:
        _print_batch_summary(batch_result)

    return batch_result


def _print_batch_summary(batch: dict) -> None:
    """印出本次批次掃描的彙總報表。"""
    print()
    print("=" * 60)
    print("批次掃描彙總")
    print("=" * 60)
    print(f"scan_run_id      : {batch['scan_run_id']}")
    print(f"狀態             : {batch['status']}")
    print(f"NVR 總數         : {batch['total_nvrs']}")
    print(f"  成功           : {batch['ok_nvrs']}")
    print(f"  失敗           : {batch['failed_nvrs']}")
    print(f"攝影機總數       : {batch['total_cameras']}")
    print(f"異常攝影機總數   : {batch['abnormal_cameras']}")
    print(f"開始時間         : {batch['started_at']}")
    print(f"結束時間         : {batch['finished_at']}")

    if batch["failures"]:
        print()
        print("[失敗清單]")
        for f in batch["failures"]:
            print(
                f"  - {f['nvr_id']} ({f['nvr_name']}): "
                f"{f['type']}: {f['error']}"
            )


# === CLI 入口（方便直接測試） ===
def _cli() -> int:
    """直接執行 batch_scan.py 時的 CLI 入口（測試用）。

    Phase 2.7+ 起：從 DB nvr_servers 表讀啟用的 NVR。
    若 DB 內無 NVR，從 nvr_config.json seed（一次性，向後相容）。
    """
    import os
    from getpass import getpass
    from pathlib import Path

    from db.sqlite_writer import SqliteWriter
    from nvr_scanner import _load_config, get_credential, load_env_file
    from web import db as webdb

    here = Path(__file__).parent
    config_path = here / "nvr_config.json"
    env_path = here / ".env"

    loaded = load_env_file(env_path)
    if loaded:
        print(f"[INFO] 已從 .env 載入 {loaded} 個環境變數")

    # 嘗試載入 nvr_config.json（可能失敗 — DB 是唯一來源時也允許）
    cfg: dict = {"scan_settings": {}, "nvr_servers": []}
    try:
        cfg = _load_config(config_path)
    except ScannerError:
        pass

    db_path = cfg["scan_settings"].get("db_path", "./nvr_scan.db")
    writer = SqliteWriter(db_path)
    print(f"[INFO] DB 路徑：{db_path}")

    # 從 DB 讀啟用的 NVR（單一 source of truth）
    enabled = webdb.list_enabled_nvrs(db_path)
    if not enabled and cfg.get("nvr_servers"):
        # DB 空 + 有 nvr_config.json → 一次性 seed 進 DB（向後相容）
        print("[INFO] DB 內無啟用 NVR，從 nvr_config.json seed…")
        seed_list = [s for s in cfg["nvr_servers"] if s.get("enabled")]
        if seed_list:
            webdb.bulk_upsert_nvrs(db_path, seed_list)
            enabled = webdb.list_enabled_nvrs(db_path)
            print(f"[INFO] 已 seed {len(enabled)} 台 NVR")

    if not enabled:
        print("[FAIL] DB 沒有啟用的 NVR", file=sys.stderr)
        return 1

    try:
        credentials = {
            "user_nonce": get_credential(
                "AVIGILON_USER_NONCE", "請輸入 userNonce: "
            ),
            "user_key": get_credential(
                "AVIGILON_USER_KEY", "請輸入 userKey: ", hide=True
            ),
            "integration_id": os.environ.get("AVIGILON_INTEGRATION_ID", ""),
            "username_override": os.environ.get("AVIGILON_USERNAME"),
            "password_override": os.environ.get("AVIGILON_PASSWORD"),
        }
    except (KeyboardInterrupt, EOFError):
        print("\n[FAIL] 認證輸入中斷", file=sys.stderr)
        return 1

    try:
        batch_scan(
            {"nvr_servers": enabled},
            credentials,
            writer,
            timeout=cfg["scan_settings"].get("timeout_seconds", DEFAULT_TIMEOUT),
        )
        return 0
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
