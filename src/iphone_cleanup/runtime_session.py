"""Persist in-process session state to disk for one ``scripts/run.sh`` execution.

Survives browser refresh and brief mount-point probe flakiness while scans or
deletes are running. Duplicate group payloads stay in ``user_scans/``; this file
holds phase, jobs, activity log, keeper choices, and fuzzy-roll cursor state.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from iphone_cleanup.state import Phase

if TYPE_CHECKING:
    from iphone_cleanup.app_context import AppCtx

_RUNTIME_NAME = "runtime_session.json"
_VERSION = 1
_last_persist_at = 0.0
_PERSIST_MIN_INTERVAL_SEC = 0.75


def runtime_path(settings: Any) -> Path:
    return settings.data_dir / _RUNTIME_NAME


def _serialize(ctx: AppCtx) -> dict[str, Any]:
    with ctx.state.lock:
        jobs = [j.to_dict() for j in ctx.state.jobs.values()]
        return {
            "version": _VERSION,
            "saved_at": time.time(),
            "run_session_id": getattr(ctx, "run_session_id", "") or "",
            "phase": ctx.state.phase.value,
            "last_error": ctx.state.last_error,
            "device_info": ctx.state.device_info,
            "mount_udid": ctx.state.mount_udid,
            "mount_path": str(ctx.state.mount_path) if ctx.state.mount_path else None,
            "scan_artifact_path": str(ctx.state.scan_artifact_path)
            if ctx.state.scan_artifact_path
            else None,
            "active_scan_session_id": ctx.state.active_scan_session_id,
            "active_exact_scan_session_id": ctx.state.active_exact_scan_session_id,
            "active_fuzzy_scan_session_id": ctx.state.active_fuzzy_scan_session_id,
            "scan_running_kind": ctx.state.scan_running_kind,
            "scan_cancel_requested": ctx.state.scan_cancel_requested,
            "fuzzy_roll_next_start": ctx.state.fuzzy_roll_next_start,
            "fuzzy_roll_total": ctx.state.fuzzy_roll_total,
            "library_indexed_count": ctx.state.library_indexed_count,
            "pending_rescan_kind": ctx.state.pending_rescan_kind,
            "last_delete_ledger": ctx.state.last_delete_ledger,
            "document_last_ledger": ctx.state.document_last_ledger,
            "group_keep": dict(ctx.state.group_keep),
            "activity_log": list(ctx.state.activity_log),
            "activity_log_seq": ctx.state.activity_log_seq,
            "jobs": jobs,
        }


def persist_runtime(ctx: AppCtx, *, force: bool = False) -> None:
    """Write runtime session to ``data/runtime_session.json`` (debounced unless *force*)."""
    global _last_persist_at
    now = time.time()
    if not force and now - _last_persist_at < _PERSIST_MIN_INTERVAL_SEC:
        return
    path = runtime_path(ctx.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = _serialize(ctx)
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    _last_persist_at = now


def clear_runtime(settings: Any) -> None:
    path = runtime_path(settings)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _apply_payload(ctx: AppCtx, data: dict[str, Any]) -> None:
    phase_raw = str(data.get("phase") or Phase.idle.value)
    try:
        phase = Phase(phase_raw)
    except ValueError:
        phase = Phase.idle

    mount_raw = data.get("mount_path")
    mount_path = Path(str(mount_raw)) if mount_raw else None

    jobs_in = data.get("jobs") or []
    from iphone_cleanup.state import JobStatus

    jobs: dict[str, JobStatus] = {}
    for raw in jobs_in:
        if not isinstance(raw, dict):
            continue
        jid = str(raw.get("job_id") or "")
        if not jid:
            continue
        was_running = bool(raw.get("running"))
        msg = str(raw.get("message") or "")
        if was_running:
            running = False
            if msg:
                msg = f"{msg} (interrupted — app restarted)"
            else:
                msg = "Interrupted — app restarted while this job was running."
        else:
            running = False
        jobs[jid] = JobStatus(
            job_id=jid,
            kind=str(raw.get("kind") or "unknown"),
            label=str(raw.get("label") or ""),
            running=running,
            message=msg,
            progress_current=raw.get("progress_current"),
            progress_total=raw.get("progress_total"),
            started_at=float(raw.get("started_at") or time.time()),
            finished_at=raw.get("finished_at"),
        )

    if phase == Phase.scanning:
        phase = Phase.reviewing if data.get("active_scan_session_id") else Phase.mounted

    artifact_raw = data.get("scan_artifact_path")
    artifact = Path(str(artifact_raw)) if artifact_raw else None

    with ctx.state.lock:
        ctx.state.phase = phase
        ctx.state.last_error = str(data.get("last_error") or "")
        ctx.state.device_info = data.get("device_info") if isinstance(data.get("device_info"), dict) else None
        ctx.state.mount_udid = data.get("mount_udid")
        ctx.state.mount_path = mount_path
        ctx.state.scan_artifact_path = artifact
        ctx.state.active_scan_session_id = data.get("active_scan_session_id")
        ctx.state.active_exact_scan_session_id = data.get("active_exact_scan_session_id")
        ctx.state.active_fuzzy_scan_session_id = data.get("active_fuzzy_scan_session_id")
        ctx.state.scan_running_kind = data.get("scan_running_kind")
        ctx.state.scan_cancel_requested = bool(data.get("scan_cancel_requested"))
        ctx.state.fuzzy_roll_next_start = int(data.get("fuzzy_roll_next_start") or 0)
        ft = data.get("fuzzy_roll_total")
        ctx.state.fuzzy_roll_total = int(ft) if ft is not None else None
        lic = data.get("library_indexed_count")
        ctx.state.library_indexed_count = int(lic) if lic is not None else None
        ctx.state.pending_rescan_kind = data.get("pending_rescan_kind")
        ctx.state.last_delete_ledger = data.get("last_delete_ledger")
        ctx.state.document_last_ledger = data.get("document_last_ledger")
        gk = data.get("group_keep")
        ctx.state.group_keep = dict(gk) if isinstance(gk, dict) else {}
        log_lines = data.get("activity_log")
        if isinstance(log_lines, list):
            from collections import deque

            ctx.state.activity_log = deque((str(x) for x in log_lines), maxlen=2048)
        seq = data.get("activity_log_seq")
        if isinstance(seq, int) and seq >= 0:
            ctx.state.activity_log_seq = seq
        ctx.state.jobs = jobs


def load_runtime(ctx: AppCtx) -> bool:
    """Restore runtime session from disk. Returns True if a file was loaded."""
    path = runtime_path(ctx.settings)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return False
    saved_run = str(data.get("run_session_id") or "")
    active_run = getattr(ctx, "run_session_id", "") or ""
    if saved_run and active_run and saved_run != active_run:
        return False
    _apply_payload(ctx, data)
    ctx.state.append_activity("SESSION | restored runtime state from data/runtime_session.json")
    return True


def reconcile_busy_mount(ctx: AppCtx) -> None:
    """Re-apply saved mount path when host probes are flaky during active work."""
    path = runtime_path(ctx.settings)
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    saved_mount = data.get("mount_path")
    if not saved_mount:
        return
    with ctx.state.lock:
        phase = ctx.state.phase
        cur = ctx.state.mount_path
    if phase not in (
        Phase.mounting,
        Phase.scanning,
        Phase.deleting,
        Phase.unmounting,
    ):
        return
    if cur is not None:
        return
    try:
        mp = Path(str(saved_mount))
    except (TypeError, ValueError):
        return
    if mp.is_dir():
        with ctx.state.lock:
            ctx.state.mount_path = mp.resolve()
