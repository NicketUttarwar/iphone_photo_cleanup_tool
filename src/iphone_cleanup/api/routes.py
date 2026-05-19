"""HTTP API and HTML pages."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from iphone_cleanup import (
    auto_best,
    delete as delmod,
    device_bridge,
    documents,
    group_keep_util,
    group_size_filter,
    mount,
    scan,
    scan_sessions,
    thumbnails,
)
from iphone_cleanup.app_context import AppCtx
from iphone_cleanup.app_log import log_event
from iphone_cleanup.run_session import is_run_session_active
from iphone_cleanup.runtime_session import persist_runtime, reconcile_busy_mount
from iphone_cleanup.session_bootstrap import sync_device, sync_mount_from_disk
from iphone_cleanup.state import Phase

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ctx(request: Request) -> AppCtx:
    return request.app.state.ctx


def _touch(ctx: AppCtx) -> None:
    ctx.state.next_event_seq()
    persist_runtime(ctx)


def _sync_runtime(ctx: AppCtx) -> None:
    sync_device(ctx)
    sync_mount_from_disk(ctx)
    reconcile_busy_mount(ctx)


def _enriched_snapshot(ctx: AppCtx) -> dict[str, Any]:
    snap = ctx.state.snapshot()
    snap["run_session_id"] = ctx.run_session_id
    snap["run_session_active"] = is_run_session_active(ctx)
    snap["document_batches"] = documents.list_batches(ctx.settings.data_dir)
    snap["fuzzy_roll_batch_size"] = ctx.settings.fuzzy_roll_batch_size
    snap["groups_page_size"] = ctx.settings.groups_page_size
    snap["preview_page_size"] = ctx.settings.preview_page_size
    snap["images_per_group_page"] = ctx.settings.images_per_group_page
    snap["review_thumbnail_max_edge"] = ctx.settings.review_thumbnail_max_edge
    try:
        snap["configured_mount_point"] = str(ctx.settings.mount_point.resolve())
    except OSError:
        snap["configured_mount_point"] = str(ctx.settings.mount_point)
    return snap


def _run_mount_thread(ctx: AppCtx, job_id: str, udid: str | None) -> None:
    mp = ctx.settings.mount_point
    try:

        def on_status(msg: str) -> None:
            ctx.state.update_job(job_id, msg, progress_current=None, progress_total=None)

        ok, msg, proc = mount.mount_media(
            ctx.settings.ifuse,
            mp,
            udid,
            status_callback=on_status,
        )
        with ctx.state.lock:
            phase_now = ctx.state.phase
            existing_mount = ctx.state.mount_path
        if phase_now != Phase.mounting:
            if ok and proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            if ok and mount.is_mountpoint(mp) and existing_mount is None:
                mount.unmount_path(mp)
            ctx.state.finish_job(job_id, "Mount stopped because the session changed (for example unmount ran first).")
            _touch(ctx)
            log_event("mount_aborted_phase", job_id=job_id, phase=phase_now.value)
            return
        if not ok:
            ctx.state.last_error = msg
            ctx.state.finish_job(job_id, msg)
            ctx.state.mount_path = None
            ctx.state.mount_udid = None
            ctx.state.ifuse_proc = None
            ctx.state.set_phase(Phase.device_detected)
            _touch(ctx)
            log_event("mount_fail", detail=msg)
            return
        ctx.state.mount_path = mp.resolve()
        ctx.state.mount_udid = udid
        ctx.state.ifuse_proc = proc
        ctx.state.scan_cancel_event.clear()
        with ctx.state.lock:
            ctx.state.scan_cancel_requested = False
            ctx.state.pending_rescan_kind = None
        ctx.state.set_phase(Phase.mounted)
        ctx.state.last_error = ""
        ctx.state.fuzzy_roll_next_start = 0
        ctx.state.fuzzy_roll_total = None
        ctx.state.finish_job(job_id, msg or "Mounted.")
        _touch(ctx)
        log_event("mount_ok", udid=str(udid), mount_path=str(ctx.state.mount_path))
    except Exception as e:
        ctx.state.last_error = str(e)
        ctx.state.finish_job(job_id, str(e))
        ctx.state.mount_path = None
        ctx.state.mount_udid = None
        ctx.state.ifuse_proc = None
        ctx.state.set_phase(Phase.device_detected)
        _touch(ctx)
        log_event("mount_error", job_id=job_id, error=str(e))


class SelectionBody(BaseModel):
    group_id: str
    keep_path: Optional[str] = None
    keep_paths: Optional[list[str]] = None
    toggle_path: Optional[str] = None


class DeleteBody(BaseModel):
    paths: list[str] = Field(default_factory=list)
    confirm: str


class DocumentRemoveBody(BaseModel):
    scope: Literal["all", "older_than_90d"]
    confirm: str
    include_visual_fallback: bool = False


class DocumentBatchBody(BaseModel):
    batch_id: Optional[str] = None


class ScanSessionActivateBody(BaseModel):
    session_id: str


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    ctx = _ctx(request)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "poll_ms": ctx.settings.sse_poll_interval_ms,
        },
    )


@router.get("/prerequisites", response_class=HTMLResponse)
def prerequisites(request: Request) -> Any:
    return templates.TemplateResponse("prerequisites.html", {"request": request})


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/status")
def api_status(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    _sync_runtime(ctx)
    snap = _enriched_snapshot(ctx)
    snap["keep_mode"] = ctx.effective_keep_mode()
    snap["status_poll_interval_ms"] = ctx.settings.sse_poll_interval_ms
    return snap


@router.post("/api/activity-log/clear")
def api_activity_log_clear(request: Request) -> dict[str, Any]:
    """Clear the rolling activity log in the UI (does not cancel jobs or change phase)."""
    ctx = _ctx(request)
    ctx.state.clear_activity_log()
    _touch(ctx)
    return {"ok": True, "message": "Activity log cleared."}


@router.get("/api/device")
def api_device(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    dev = sync_device(ctx, log_check=True)
    sync_mount_from_disk(ctx)
    _touch(ctx)
    return dev


@router.post("/api/mount")
def api_mount(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if ctx.state.phase == Phase.mounting:
        raise HTTPException(409, "Mount already in progress — watch live progress at the top of the page.")
    dev = ctx.state.device_info or device_bridge.detect_device(ctx.settings.ideviceinfo, ctx.settings.idevice_id)
    if not dev.get("trusted"):
        raise HTTPException(400, dev.get("error") or "Device not ready for mount.")
    udid = dev.get("udid")
    mp = ctx.settings.mount_point
    if mount.is_mountpoint(mp):
        ctx.state.mount_path = mp.resolve()
        ctx.state.mount_udid = udid
        ctx.state.ifuse_proc = None
        ctx.state.scan_cancel_event.clear()
        with ctx.state.lock:
            ctx.state.scan_cancel_requested = False
            ctx.state.pending_rescan_kind = None
        ctx.state.set_phase(Phase.mounted)
        ctx.state.last_error = ""
        ctx.state.append_activity(
            f"MOUNT SHORT-CIRCUIT | {mp.resolve()} was already mounted — session state refreshed | udid={udid!r}"
        )
        _touch(ctx)
        log_event("mount_ok", udid=str(udid), mount_path=str(ctx.state.mount_path))
        return {"ok": True, "message": "Already mounted at this path.", "mount_path": str(ctx.state.mount_path)}
    ctx.state.append_activity(
        f"MOUNT REQUEST | operator started mount | mount_point={mp.resolve()} | udid={udid!r} | "
        f"entering_phase=mounting"
    )
    ctx.state.set_phase(Phase.mounting)
    job = ctx.state.start_job("mount", "Mounting iPhone media…")
    t = threading.Thread(target=_run_mount_thread, args=(ctx, job.job_id, udid), daemon=True)
    t.start()
    _touch(ctx)
    return {
        "ok": True,
        "started": True,
        "message": "Mount started — watch live progress and the activity log at the top of the page.",
        "job_id": job.job_id,
    }


@router.post("/api/unmount")
def api_unmount(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    mp = ctx.settings.mount_point
    # Let any in-flight duplicate scan exit quickly; avoids applying results after the volume is gone.
    ctx.state.scan_cancel_event.set()
    with ctx.state.lock:
        ctx.state.pending_rescan_kind = None
    ctx.state.append_activity(
        "UNMOUNT REQUEST | operator asked to unmount — signaling any in-flight scan to cancel; "
        f"mount_point_config={mp.resolve()}"
    )
    if mount.is_mountpoint(mp):
        ctx.state.set_phase(Phase.unmounting)
    ok, msg = mount.unmount_path(mp)
    udid_for_cache = ctx.state.mount_udid
    mp_for_cache = ctx.state.mount_path
    if ok:
        if udid_for_cache and mp_for_cache:
            try:
                cache_p = scan.fuzzy_roll_cache_path(
                    ctx.settings.scan_artifacts_dir,
                    udid_for_cache,
                    mp_for_cache,
                )
                cache_p.unlink(missing_ok=True)
            except OSError:
                pass
        ctx.state.mount_path = None
        ctx.state.mount_udid = None
        ctx.state.ifuse_proc = None
        ctx.state.duplicate_groups = []
        ctx.state.library_indexed_count = None
        ctx.state.group_keep = {}
        ctx.state.scan_artifact_path = None
        ctx.state.fuzzy_roll_next_start = 0
        ctx.state.fuzzy_roll_total = None
        ctx.state.set_phase(Phase.idle)
        ctx.state.last_error = ""
        n_batches = documents.finalize_all_batches(ctx.settings.data_dir)
        cleared = thumbnails.clear_jpeg_cache(ctx.settings.thumbnail_cache_dir)
        log_event("unmount_finalize", quarantine_batches_dropped=n_batches, thumbnail_cache_files_cleared=cleared)
        ctx.state.append_activity(
            f"UNMOUNT OK | volume released | document_batches_dropped={n_batches} | "
            f"thumbnail_cache_files_cleared={cleared} | session_reset_to_idle"
        )
    else:
        ctx.state.last_error = msg
        if mount.is_mountpoint(mp):
            ctx.state.set_phase(Phase.mounted)
        else:
            ctx.state.mount_path = None
            ctx.state.mount_udid = None
            ctx.state.ifuse_proc = None
            ctx.state.set_phase(Phase.idle)
        ctx.state.append_activity(
            f"UNMOUNT FAILED | still_mounted_at_config_path={mount.is_mountpoint(mp)} | detail={msg!r}"
        )
    _touch(ctx)
    log_event("unmount", ok=ok, detail=msg)
    return {"ok": ok, "message": msg}


def _relpath_if_under(full_path: str, mount_root: Path) -> str | None:
    try:
        return str(Path(full_path).resolve().relative_to(mount_root.resolve()))
    except ValueError:
        return None


def _thumbnail_roots(ctx: AppCtx) -> list[Path]:
    """Mount directories used to resolve review thumbnails (live mount, config path, saved sessions)."""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: Path | None) -> None:
        if p is None:
            return
        try:
            r = p.resolve()
        except OSError:
            return
        key = str(r)
        if key in seen:
            return
        seen.add(key)
        roots.append(r)

    add(ctx.state.mount_path)
    add(ctx.settings.mount_point)
    us_root = ctx.settings.user_scans_dir
    for sid in (
        ctx.state.active_exact_scan_session_id,
        ctx.state.active_fuzzy_scan_session_id,
        ctx.state.active_scan_session_id,
    ):
        if not sid:
            continue
        try:
            manifest, _, _, _ = scan_sessions.load_session_groups(us_root, sid)
        except (OSError, ValueError, FileNotFoundError):
            continue
        mp = manifest.get("mount_path")
        if mp:
            add(Path(str(mp)))
    return roots


def _relpath_under_roots(full_path: str, roots: list[Path]) -> str:
    try:
        full = Path(full_path).resolve()
    except OSError:
        return ""
    for root in roots:
        try:
            return str(full.relative_to(root.resolve()))
        except ValueError:
            continue
    return ""


def _resolve_thumbnail_file(ctx: AppCtx, relpath: str) -> tuple[Path, Path]:
    if ".." in Path(relpath).parts:
        raise HTTPException(400, "Invalid path.")
    rel = relpath.replace("\\", "/").lstrip("/")
    for root in _thumbnail_roots(ctx):
        full = (root / rel).resolve()
        try:
            full.relative_to(root.resolve())
        except ValueError:
            continue
        if full.is_file():
            return full, root.resolve()
    raise HTTPException(404, "Not found.")


def _groups_for_kind(ctx: AppCtx, scan_kind: Literal["exact", "fuzzy"]) -> list[dict[str, Any]]:
    return scan_sessions.filter_groups_by_kind(ctx.state.duplicate_groups, scan_kind)


def _parse_size_filter(raw: str | None) -> group_size_filter.GroupSizeFilter:
    try:
        return group_size_filter.normalize_size_filter(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _review_groups(
    ctx: AppCtx,
    scan_kind: Literal["exact", "fuzzy"],
    size_filter: group_size_filter.GroupSizeFilter,
) -> list[dict[str, Any]]:
    kind_groups = _groups_for_kind(ctx, scan_kind)
    return group_size_filter.filter_groups_by_size(kind_groups, size_filter)


def _duplicate_paths_to_delete(
    ctx: AppCtx,
    scan_kind: Literal["exact", "fuzzy"] | None = None,
    size_filter: group_size_filter.GroupSizeFilter = "all",
) -> list[str]:
    if scan_kind is not None:
        groups = _review_groups(ctx, scan_kind, size_filter)
    else:
        groups = ctx.state.duplicate_groups
        if size_filter != "all":
            groups = group_size_filter.filter_groups_by_size(groups, size_filter)
    to_delete: list[str] = []
    for g in groups:
        keep_set = group_keep_util.keep_paths_set(ctx, g)
        for p in g.get("paths") or []:
            if str(p) not in keep_set:
                to_delete.append(str(p))
    return to_delete


def _scan_publish_stale(ctx: AppCtx, job_id: str, root: Path) -> bool:
    """Return True if the scan job must not publish results (unmount, etc.)."""
    try:
        root_r = root.resolve()
    except OSError:
        root_r = root
    with ctx.state.lock:
        mp = ctx.state.mount_path
        ph = ctx.state.phase
    if ph != Phase.scanning:
        ctx.state.finish_job(
            job_id,
            "Scan stopped — another action changed the session (for example unmount).",
        )
        _touch(ctx)
        log_event("scan_publish_skipped", job_id=job_id, phase=ph.value)
        return True
    if mp is None:
        ctx.state.finish_job(job_id, "Scan stopped — no volume mounted.")
        ctx.state.set_phase(Phase.idle)
        _touch(ctx)
        log_event("scan_publish_skipped", job_id=job_id, reason="no_mount")
        return True
    try:
        cur = Path(mp).resolve()
    except OSError:
        cur = None
    if cur != root_r:
        ctx.state.finish_job(job_id, "Scan stopped — mount path changed.")
        ctx.state.set_phase(Phase.mounted)
        _touch(ctx)
        log_event("scan_publish_skipped", job_id=job_id, reason="mount_mismatch")
        return True
    return False


def _maybe_start_pending_rescan(ctx: AppCtx) -> None:
    """If the operator queued another scan while one was running, start it here (single worker chain)."""
    with ctx.state.lock:
        pending = ctx.state.pending_rescan_kind
        ctx.state.pending_rescan_kind = None
    if not pending:
        return
    root = ctx.state.mount_path
    if pending not in ("exact", "fuzzy"):
        log_event("scan_pending_dropped", reason="bad_kind", kind=pending)
        return
    if ctx.state.phase not in (Phase.mounted, Phase.reviewing):
        log_event("scan_pending_dropped", reason="phase", phase=ctx.state.phase.value)
        return
    if not root or not root.is_dir():
        log_event("scan_pending_dropped", reason="no_mount")
        return
    ctx.state.scan_cancel_event.clear()
    with ctx.state.lock:
        ctx.state.scan_cancel_requested = False
    ctx.state.set_phase(Phase.scanning)
    with ctx.state.lock:
        ctx.state.scan_running_kind = pending
    log_event("scan_start_chained", scan_kind=pending)
    t = threading.Thread(target=_run_scan_thread, args=(ctx, pending, False), daemon=True)
    t.start()
    _touch(ctx)


def _strip_fuzzy_groups(ctx: AppCtx) -> None:
    """Remove fuzzy burst groups and their selection entries (used for fuzzy_restart)."""
    with ctx.state.lock:
        old_ids = {str(g["id"]) for g in ctx.state.duplicate_groups if str(g.get("scan_kind") or "exact") == "fuzzy"}
        ctx.state.duplicate_groups = [
            g for g in ctx.state.duplicate_groups if str(g.get("scan_kind") or "exact") != "fuzzy"
        ]
        for oid in old_ids:
            ctx.state.group_keep.pop(oid, None)


def _apply_auto_groups(ctx: AppCtx) -> None:
    root = ctx.state.mount_path
    if not root:
        return
    face = ctx.settings.duplicates_auto_face_eye
    max_img = ctx.settings.duplicates_face_eye_max_images
    for g in ctx.state.duplicate_groups:
        paths = list(g.get("paths") or [])
        if not paths:
            continue
        gid = str(g["id"])
        if str(g.get("scan_kind") or "exact") == "fuzzy":
            kps = auto_best.pick_fuzzy_keepers_by_eye_count(paths)
        else:
            keep = auto_best.pick_recommended(
                paths,
                face_eye=face,
                face_eye_max_images=max_img,
            )
            kps = [keep] if keep else [paths[0]]
        g["recommendedKeeps"] = list(kps)
        g["recommendedKeep"] = kps[0]
        ctx.state.group_keep[gid] = list(kps)
    _touch(ctx)


def _run_scan_thread(
    ctx: AppCtx,
    scan_kind: Literal["exact", "fuzzy"],
    fuzzy_restart: bool = False,
) -> None:
    job_label = (
        "Fuzzy roll batch (bursts & same-scene sets)…"
        if scan_kind == "fuzzy"
        else "Scanning library for duplicates…"
    )
    with ctx.state.lock:
        ctx.state.scan_running_kind = scan_kind
    job = ctx.state.start_job("scan", job_label)
    try:
        root = ctx.state.mount_path
        if not root or not root.is_dir():
            ctx.state.finish_job(job.job_id, "No mount path.")
            ctx.state.set_phase(Phase.mounted)
            _touch(ctx)
            return
        cancelled = False
        groups: list[dict[str, Any]] = []
        fuzzy_next = 0
        fuzzy_total = 0
        batch_lo = 0
        with ctx.state.lock:
            fz_next_snap = ctx.state.fuzzy_roll_next_start
        ctx.state.append_activity(
            f"SCAN WORKER | job_id={job.job_id} | scan_kind={scan_kind} | fuzzy_restart={fuzzy_restart} | "
            f"mount_root={root.resolve()} | fuzzy_roll_next_start={fz_next_snap} | "
            f"fuzzy_roll_batch_size={ctx.settings.fuzzy_roll_batch_size} | mount_udid={ctx.state.mount_udid!r} | "
            f"exact_phash_threshold={ctx.settings.phash_threshold} | fuzzy_adjacent_hamming_max="
            f"{ctx.settings.fuzzy_phash_max_hamming} | fuzzy_phash_max_dim={ctx.settings.fuzzy_phash_max_dim} | "
            f"fuzzy_palette_grid={ctx.settings.fuzzy_palette_grid} | scipy_palette_emd="
            f"{scan.fuzzy_palette_scipy_status()}"
        )

        last_prog_touch = 0.0

        def prog(cur: int, total: int, msg: str) -> None:
            if total > 0:
                ctx.state.update_job(job.job_id, msg, progress_current=cur, progress_total=total)
            else:
                ctx.state.update_job(job.job_id, msg, progress_current=None, progress_total=None)
            nonlocal last_prog_touch
            now = time.time()
            if now - last_prog_touch >= 1.5:
                last_prog_touch = now
                _touch(ctx)

        try:
            if scan_kind == "fuzzy":
                if fuzzy_restart:
                    _strip_fuzzy_groups(ctx)
                    with ctx.state.lock:
                        ctx.state.fuzzy_roll_next_start = 0
                with ctx.state.lock:
                    batch_lo = ctx.state.fuzzy_roll_next_start
                groups, fuzzy_next, fuzzy_total = scan.run_fuzzy_roll_scan_batch(
                    root,
                    scan_artifacts_dir=ctx.settings.scan_artifacts_dir,
                    mount_udid=ctx.state.mount_udid,
                    batch_start=batch_lo,
                    batch_size=ctx.settings.fuzzy_roll_batch_size,
                    phash_max_dim=ctx.settings.fuzzy_phash_max_dim,
                    max_adjacent_hamming=ctx.settings.fuzzy_phash_max_hamming,
                    fuzzy_match_config=scan.fuzzy_match_config_from_settings(ctx.settings),
                    fuzzy_roll_sort_exif=ctx.settings.fuzzy_roll_sort_exif,
                    progress_callback=prog,
                    cancel_event=ctx.state.scan_cancel_event,
                )
                with ctx.state.lock:
                    ctx.state.library_indexed_count = fuzzy_total
            else:
                groups, indexed = scan.scan_duplicates(
                    root,
                    ctx.settings.phash_threshold,
                    phash_max_dim=ctx.settings.fuzzy_phash_max_dim,
                    exact_max_hash_cluster=ctx.settings.exact_max_hash_cluster,
                    progress_callback=prog,
                    cancel_event=ctx.state.scan_cancel_event,
                    walk_progress_every=ctx.settings.walk_progress_every,
                )
                with ctx.state.lock:
                    ctx.state.library_indexed_count = indexed
        except scan.ScanCancelled as e:
            if scan_kind == "fuzzy":
                groups = scan.finalize_groups(e.partial_groups, scan_kind="fuzzy")
            else:
                groups = scan.finalize_duplicate_groups(e.partial_groups)
            cancelled = True
        except Exception as e:
            ctx.state.last_error = str(e)
            ctx.state.finish_job(job.job_id, str(e))
            ctx.state.set_phase(
                Phase.reviewing if ctx.state.duplicate_groups else Phase.mounted
            )
            _touch(ctx)
            log_event("scan_error", job_id=job.job_id, error=str(e))
            return

        if _scan_publish_stale(ctx, job.job_id, root):
            return

        had_any_groups_prior = bool(ctx.state.duplicate_groups)

        if scan_kind == "fuzzy" and not cancelled:
            with ctx.state.lock:
                ctx.state.fuzzy_roll_next_start = fuzzy_next
                ctx.state.fuzzy_roll_total = fuzzy_total

        if scan_kind == "exact":
            with ctx.state.lock:
                ctx.state.fuzzy_roll_next_start = 0
                ctx.state.fuzzy_roll_total = None
                fuzzy_kept = scan_sessions.filter_groups_by_kind(ctx.state.duplicate_groups, "fuzzy")
                old_exact_ids = {
                    str(g["id"])
                    for g in ctx.state.duplicate_groups
                    if scan_sessions.group_scan_kind(g) == "exact"
                }
                ctx.state.duplicate_groups = fuzzy_kept + groups
                for gid in old_exact_ids:
                    ctx.state.group_keep.pop(gid, None)
            _apply_auto_groups(ctx)
        else:
            with ctx.state.lock:
                exact_kept = scan_sessions.filter_groups_by_kind(ctx.state.duplicate_groups, "exact")
                fuzzy_kept = scan_sessions.filter_groups_by_kind(ctx.state.duplicate_groups, "fuzzy")
                ctx.state.duplicate_groups = exact_kept + fuzzy_kept + groups
            for g in groups:
                gid = str(g["id"])
                paths = list(g.get("paths") or [])
                if not paths:
                    continue
                kps = auto_best.pick_fuzzy_keepers_by_eye_count(paths)
                g["recommendedKeeps"] = list(kps)
                g["recommendedKeep"] = kps[0]
                ctx.state.group_keep[gid] = list(kps)
        with ctx.state.lock:
            groups_to_save = scan_sessions.filter_groups_by_kind(
                list(ctx.state.duplicate_groups), scan_kind
            )
        save_ids = {str(g["id"]) for g in groups_to_save}
        gk_save = {
            gid: list(paths) for gid, paths in ctx.state.group_keep.items() if gid in save_ids
        }
        manifest = scan_sessions.persist_session(
            ctx.settings.user_scans_dir,
            groups=groups_to_save,
            scan_kind=scan_kind,
            mount_udid=ctx.state.mount_udid,
            mount_path=ctx.state.mount_path,
            group_keep=gk_save,
        )
        ctx.state.scan_artifact_path = Path(str(manifest["results_path"]))
        sid = str(manifest["id"])
        ctx.state.active_scan_session_id = sid
        with ctx.state.lock:
            if scan_kind == "exact":
                ctx.state.active_exact_scan_session_id = sid
            else:
                ctx.state.active_fuzzy_scan_session_id = sid
        ctx.state.append_activity(
            f"SCAN SESSION | saved {manifest['id']!r} ({manifest.get('group_count', 0)} groups) under user_scans/"
        )
        if cancelled and len(groups) == 0:
            if scan_kind == "fuzzy" and had_any_groups_prior:
                ctx.state.set_phase(Phase.reviewing)
                ctx.state.finish_job(job.job_id, "Fuzzy batch cancelled (no new groups this run).")
                log_event("scan_cancelled", job_id=job.job_id, groups=0, scan_kind=scan_kind)
            else:
                ctx.state.set_phase(Phase.mounted)
                ctx.state.finish_job(job.job_id, "Scan cancelled (no groups yet).")
                log_event("scan_cancelled_empty", job_id=job.job_id)
        else:
            ctx.state.set_phase(Phase.reviewing)
            if cancelled:
                ctx.state.finish_job(
                    job.job_id,
                    f"Scan cancelled — {len(groups)} group(s) so far.",
                )
                log_event("scan_cancelled", job_id=job.job_id, groups=len(groups), scan_kind=scan_kind)
            else:
                if scan_kind == "fuzzy":
                    if fuzzy_total == 0:
                        done_msg = "Fuzzy: no images found under the mount."
                    elif not groups and fuzzy_next >= fuzzy_total:
                        done_msg = (
                            "Fuzzy: no burst groups in this slice; the roll is fully scanned. "
                            "Run Fuzzy roll scan again to clear fuzzy groups and rescan "
                            "(reuses cached hashes on this Mac)."
                        )
                    elif not groups:
                        done_msg = (
                            f"Fuzzy batch: no burst groups in photos {batch_lo}–{fuzzy_next} "
                            f"of {fuzzy_total}. Run another batch when ready."
                        )
                    else:
                        done_msg = (
                            f"Found {len(groups)} fuzzy burst group(s) in this batch "
                            f"(roll progress {fuzzy_next}/{fuzzy_total}). "
                            "Review keep/delete, then click Fuzzy roll scan for the next chunk."
                        )
                else:
                    done_msg = f"Found {len(groups)} duplicate groups."
                ctx.state.finish_job(job.job_id, done_msg)
                log_event("scan_done", job_id=job.job_id, groups=len(groups), scan_kind=scan_kind)
        _touch(ctx)
    finally:
        with ctx.state.lock:
            ctx.state.scan_cancel_requested = False
            ctx.state.scan_running_kind = None
        _maybe_start_pending_rescan(ctx)


@router.post("/api/scan/start")
def api_scan_start(
    request: Request,
    kind: Literal["exact", "fuzzy"] = Query(
        "exact",
        description='Use "exact" for byte-identical pHash dupes, "fuzzy" for similar consecutive shots in roll order.',
    ),
    fuzzy_restart: bool = Query(
        False,
        description="Fuzzy only: clear fuzzy burst groups and re-run from roll index 0 (reuses cached pHashes).",
    ),
) -> dict[str, Any]:
    ctx = _ctx(request)
    if ctx.state.phase == Phase.scanning:
        with ctx.state.lock:
            ctx.state.pending_rescan_kind = kind
            ctx.state.scan_running_kind = kind
        ctx.state.scan_cancel_event.set()
        with ctx.state.lock:
            ctx.state.scan_cancel_requested = True
        log_event("scan_start_replace", scan_kind=kind)
        ctx.state.append_activity(
            f"SCAN REPLACE | operator started a new {kind!r} scan while another was running — cancel signaled; "
            f"pending_rescan_kind set to {kind!r}."
        )
        _touch(ctx)
        return {
            "ok": True,
            "replacing": True,
            "message": "Stopping the current scan and switching to this one.",
            "scan_kind": kind,
        }
    if ctx.state.phase == Phase.mounting:
        raise HTTPException(400, "Wait for mounting to finish before scanning.")
    if ctx.state.phase not in (Phase.mounted, Phase.reviewing):
        raise HTTPException(400, "Mount the device before scanning.")
    if kind == "fuzzy" and not fuzzy_restart:
        with ctx.state.lock:
            fz_total = ctx.state.fuzzy_roll_total
            fz_next = ctx.state.fuzzy_roll_next_start
        if fz_total is not None and fz_total > 0 and fz_next >= fz_total:
            fuzzy_restart = True
    ctx.state.scan_cancel_event.clear()
    with ctx.state.lock:
        ctx.state.scan_cancel_requested = False
        ctx.state.pending_rescan_kind = None
    phase_before = ctx.state.phase.value
    scan_detail = "exact duplicate scan" if kind == "exact" else "fuzzy roll batch"
    ctx.state.set_phase(Phase.scanning, detail=scan_detail)
    with ctx.state.lock:
        ctx.state.scan_running_kind = kind
    log_event("scan_start", scan_kind=kind)
    ctx.state.append_activity(
        f"SCAN START | kind={kind!r} | fuzzy_restart={fuzzy_restart} | phase_before={phase_before} | phase_now=scanning | "
        f"mount_path={ctx.state.mount_path!s} | duplicate_group_count={len(ctx.state.duplicate_groups)}"
    )
    t = threading.Thread(target=_run_scan_thread, args=(ctx, kind, fuzzy_restart), daemon=True)
    t.start()
    _touch(ctx)
    if kind == "fuzzy":
        if ctx.settings.fuzzy_roll_batch_size <= 0:
            msg = "Fuzzy roll scan started — indexing and hashing the entire library; progress is at the top."
        else:
            msg = (
                "Fuzzy roll batch started — only the next slice of the library is hashed; progress is at the top."
            )
    else:
        msg = "Exact duplicate scan started — walking every photo on the mount; progress is at the top of the page."
    return {"ok": True, "message": msg, "scan_kind": kind}


@router.post("/api/scan/cancel")
def api_scan_cancel(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if ctx.state.phase != Phase.scanning:
        log_event("scan_cancel_noop", phase=ctx.state.phase.value)
        _touch(ctx)
        return {"ok": True, "noop": True, "message": "No duplicate scan was running."}
    ctx.state.scan_cancel_event.set()
    with ctx.state.lock:
        ctx.state.scan_cancel_requested = True
        ctx.state.pending_rescan_kind = None
    ctx.state.append_activity(
        "SCAN CANCEL | operator requested stop — scan_cancel_event is set; in-flight scan exits between files/steps."
    )
    log_event("scan_cancel_requested")
    _touch(ctx)
    return {
        "ok": True,
        "message": "Stop requested — the scan worker exits almost immediately between photos and filesystem steps.",
    }


@router.get("/api/scan/sessions")
def api_scan_sessions(
    request: Request,
    kind: Optional[Literal["exact", "fuzzy"]] = Query(
        None,
        description='Filter saved scans to "exact" or "fuzzy". Omit to return all.',
    ),
) -> dict[str, Any]:
    ctx = _ctx(request)
    root = ctx.settings.user_scans_dir
    sessions = scan_sessions.list_sessions(root)
    if kind is not None:
        sk = scan_sessions._normalize_scan_kind(kind)
        sessions = [
            s
            for s in sessions
            if scan_sessions._normalize_scan_kind(str(s.get("scan_kind") or "exact")) == sk
        ]
        active = (
            (
                ctx.state.active_exact_scan_session_id
                if sk == "exact"
                else ctx.state.active_fuzzy_scan_session_id
            )
            or scan_sessions.default_active_id(root, sk)
        )
    else:
        active = ctx.state.active_scan_session_id or scan_sessions.default_active_id(root)
    return {
        "sessions": sessions,
        "active_session_id": active,
        "scan_kind": kind,
    }


@router.post("/api/scan/sessions/activate")
def api_scan_sessions_activate(body: ScanSessionActivateBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if ctx.state.phase == Phase.scanning:
        raise HTTPException(409, "Wait for the current scan to finish before switching sessions.")
    try:
        manifest = scan_sessions.apply_session_to_state(ctx, body.session_id.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except OSError as e:
        raise HTTPException(500, f"Could not load scan session: {e}") from e
    ctx.state.append_activity(
        f"SCAN SESSION | activated {manifest['id']!r} ({manifest.get('group_count', 0)} groups)"
    )
    _touch(ctx)
    sk = scan_sessions._normalize_scan_kind(str(manifest.get("scan_kind") or "exact"))
    kind_count = len(scan_sessions.filter_groups_by_kind(ctx.state.duplicate_groups, sk))
    return {
        "ok": True,
        "session": manifest,
        "group_count": len(ctx.state.duplicate_groups),
        "kind_group_count": kind_count,
        "scan_kind": sk,
        "exact_group_count": sum(
            1 for g in ctx.state.duplicate_groups if scan_sessions.group_scan_kind(g) == "exact"
        ),
        "fuzzy_group_count": sum(
            1 for g in ctx.state.duplicate_groups if scan_sessions.group_scan_kind(g) == "fuzzy"
        ),
        "phase": ctx.state.phase.value,
    }


@router.get("/api/scan/groups")
def api_scan_groups(
    request: Request,
    kind: Optional[Literal["exact", "fuzzy"]] = Query(
        None,
        description='Return only groups from the "exact" or "fuzzy" workflow.',
    ),
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description="Page size; defaults to ui.groups_page_size from config.",
    ),
    size_filter: str = Query(
        "all",
        description='Show only sets with this many photos: "all", "2", "3", "4", or "5plus" (5+).',
    ),
) -> dict[str, Any]:
    ctx = _ctx(request)
    sf = _parse_size_filter(size_filter)
    if kind is not None:
        kind_groups = _groups_for_kind(ctx, kind)
        all_groups = group_size_filter.filter_groups_by_size(kind_groups, sf)
        size_counts = group_size_filter.size_filter_counts(kind_groups)
    else:
        all_groups = group_size_filter.filter_groups_by_size(ctx.state.duplicate_groups, sf)
        size_counts = group_size_filter.size_filter_counts(ctx.state.duplicate_groups)
    page_size = limit if limit is not None else ctx.settings.groups_page_size
    page_size = max(1, min(500, page_size))
    total = len(all_groups)
    page = all_groups[offset : offset + page_size]
    page_ids = {str(g.get("id")) for g in page}
    keep = {gid: paths for gid, paths in ctx.state.group_keep.items() if gid in page_ids}
    thumb_roots = _thumbnail_roots(ctx)
    groups_out: list[dict[str, Any]] = []
    for g in page:
        paths = [str(x) for x in g.get("paths") or []]
        row = dict(g)
        row["relpaths"] = [_relpath_under_roots(p, thumb_roots) for p in paths]
        groups_out.append(row)
    return {
        "groups": groups_out,
        "keep": keep,
        "keep_mode": ctx.effective_keep_mode(),
        "scan_kind": kind,
        "size_filter": sf,
        "size_counts": size_counts,
        "total": total,
        "offset": offset,
        "limit": page_size,
        "has_more": offset + len(page) < total,
    }


@router.post("/api/selection")
def api_selection(body: SelectionBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    gid = body.group_id
    actions = sum(
        1
        for x in (body.toggle_path is not None, body.keep_paths is not None, body.keep_path is not None)
        if x
    )
    if actions != 1:
        raise HTTPException(400, "Provide exactly one of: keep_path, keep_paths, or toggle_path.")
    found = False
    for g in ctx.state.duplicate_groups:
        if str(g.get("id")) != gid:
            continue
        paths = [str(x) for x in g.get("paths") or []]
        path_set = set(paths)
        chosen: list[str]
        if body.toggle_path is not None:
            tp = str(body.toggle_path)
            if tp not in path_set:
                raise HTTPException(400, "toggle_path must be one of the group paths.")
            cur = group_keep_util.keep_paths_set(ctx, g)
            nxt = set(cur)
            if tp in nxt:
                if len(nxt) <= 1:
                    raise HTTPException(400, "Keep at least one image in this group.")
                nxt.remove(tp)
            else:
                nxt.add(tp)
            chosen = [p for p in paths if p in nxt]
        elif body.keep_paths is not None:
            kset = {str(p) for p in body.keep_paths}
            if not kset:
                raise HTTPException(400, "keep_paths must be non-empty.")
            if not kset <= path_set:
                raise HTTPException(400, "keep_paths must all belong to this group.")
            chosen = [p for p in paths if p in kset]
        else:
            k = str(body.keep_path)
            if k not in path_set:
                raise HTTPException(400, "keep_path must be one of the group paths.")
            chosen = [k]
        ctx.state.group_keep[gid] = chosen
        g["recommendedKeep"] = chosen[0]
        g["recommendedKeeps"] = list(chosen)
        found = True
        break
    if not found:
        raise HTTPException(404, "Unknown group.")
    for g in ctx.state.duplicate_groups:
        if str(g.get("id")) == gid:
            sk = scan_sessions.group_scan_kind(g)
            scan_sessions.sync_active_session_from_state(ctx, sk)
            break
    _touch(ctx)
    return {"ok": True}


@router.get("/api/delete/preview")
def api_delete_preview(
    request: Request,
    kind: Literal["exact", "fuzzy"] = Query(
        "exact",
        description='Preview deletions for the "exact" or "fuzzy" workflow only.',
    ),
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=500),
    size_filter: str = Query(
        "all",
        description='Preview deletions only for sets matching "all", "2", "3", "4", or "5plus".',
    ),
) -> dict[str, Any]:
    ctx = _ctx(request)
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        raise HTTPException(400, "Mount the device first.")
    sf = _parse_size_filter(size_filter)
    mroot = root.resolve()
    to_delete = _duplicate_paths_to_delete(ctx, kind, sf)
    total_bytes = 0
    for raw in to_delete:
        try:
            total_bytes += Path(raw).resolve().stat().st_size
        except OSError:
            continue
    groups_with_deletions = 0
    all_samples: list[dict[str, str]] = []
    review_groups = _review_groups(ctx, kind, sf)
    for g in review_groups:
        gid = str(g.get("id"))
        keep_set = group_keep_util.keep_paths_set(ctx, g)
        paths = [str(x) for x in g.get("paths") or []]
        has_del = any(p not in keep_set for p in paths)
        if has_del:
            groups_with_deletions += 1
        for p in paths:
            if p in keep_set:
                continue
            rel = _relpath_if_under(str(p), mroot)
            if rel:
                all_samples.append({"group_id": gid, "relpath": rel})
            break
    page_size = limit if limit is not None else ctx.settings.preview_page_size
    page_size = max(1, min(500, page_size))
    total_samples = len(all_samples)
    page = all_samples[offset : offset + page_size]
    return {
        "file_count": len(to_delete),
        "total_bytes": total_bytes,
        "duplicate_group_count": len(review_groups),
        "groups_with_deletions": groups_with_deletions,
        "thumbnail_samples": page,
        "thumbnail_sample_total": total_samples,
        "offset": offset,
        "limit": page_size,
        "has_more": offset + len(page) < total_samples,
        "scan_kind": kind,
        "size_filter": sf,
        "size_filter_label": group_size_filter.size_filter_label(sf),
    }


@router.post("/api/delete")
def api_delete(
    body: DeleteBody,
    request: Request,
    kind: Literal["exact", "fuzzy"] = Query(
        "exact",
        description='Delete only non-kept files from the "exact" or "fuzzy" workflow.',
    ),
    size_filter: str = Query(
        "all",
        description='Delete only within sets matching "all", "2", "3", "4", or "5plus".',
    ),
) -> dict[str, Any]:
    ctx = _ctx(request)
    if body.confirm != "DELETE_SELECTED_FILES":
        raise HTTPException(400, "Confirmation phrase mismatch.")
    root = ctx.state.mount_path
    if not root:
        raise HTTPException(400, "Not mounted.")
    sf = _parse_size_filter(size_filter)
    sf_label = group_size_filter.size_filter_label(sf)
    label = (
        f"Deleting fuzzy burst extras ({sf_label})…"
        if kind == "fuzzy"
        else f"Deleting exact duplicate extras ({sf_label})…"
    )
    job = ctx.state.start_job("delete", label)
    log_event("delete_job_started", job_id=job.job_id, size_filter=sf)
    ctx.state.set_phase(Phase.deleting)

    def work() -> None:
        try:
            to_delete = _duplicate_paths_to_delete(ctx, kind, sf)
            extra = [p for p in body.paths if p not in to_delete]
            preview = to_delete[:8]
            ctx.state.append_activity(
                f"DELETE JOB | scan_kind={kind!r} | size_filter={sf!r} | paths_to_delete={len(to_delete)} | "
                f"chunk_size={ctx.settings.delete_chunk_size} | first_paths_preview={preview!r}"
            )
            if extra:
                ctx.state.update_job(job.job_id, "Extra paths ignored (not in duplicate-to-delete set).")

            def prog(done: int, total: int, del_n: int, fail_n: int, skip_n: int, last_path: str = "") -> None:
                tail = Path(last_path).name if last_path else ""
                msg = f"Deleting chunk progress {done}/{total} on phone volume"
                if tail:
                    msg += f" — last_file_name={tail}"
                if last_path:
                    msg += f" | last_full_path={last_path}"
                msg += f" | running_totals deleted={del_n} failed={fail_n} skipped={skip_n}"
                ctx.state.update_job(
                    job.job_id,
                    msg,
                    progress_current=done,
                    progress_total=max(total, 1),
                )

            res = delmod.delete_paths_chunked(
                to_delete,
                root,
                ctx.settings.delete_chunk_size,
                on_progress=prog,
            )
            ctx.state.last_delete_ledger = {
                "size_filter": sf,
                "size_filter_label": sf_label,
                "deleted_count": len(res["deleted"]),
                "failed_count": len(res["failed"]),
                "skipped_count": len(res["skipped"]),
                "failed_sample": res["failed"][:40],
            }
            ctx.state.finish_job(
                job.job_id,
                f"Deleted {len(res['deleted'])} files; failed {len(res['failed'])}; skipped {len(res['skipped'])}.",
            )
            ctx.state.set_phase(Phase.reviewing if ctx.state.mount_path else Phase.idle)
            log_event(
                "delete_done",
                job_id=job.job_id,
                deleted=len(res["deleted"]),
                failed=len(res["failed"]),
                skipped=len(res["skipped"]),
            )
        except Exception as e:
            ctx.state.last_error = str(e)
            ctx.state.finish_job(job.job_id, str(e))
            ctx.state.set_phase(Phase.reviewing if ctx.state.mount_path else Phase.idle)
            log_event("delete_error", job_id=job.job_id, error=str(e))
        _touch(ctx)

    threading.Thread(target=work, daemon=True).start()
    _touch(ctx)
    return {"ok": True, "message": "Delete job started."}


def _run_document_remove_thread(
    ctx: AppCtx,
    *,
    scope: Literal["all", "older_than_90d"],
    include_visual_fallback: bool,
    job_id: str,
) -> None:
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        ctx.state.finish_job(job_id, "No mount path.")
        _touch(ctx)
        return
    try:
        paths = documents.iter_document_paths(
            root,
            scope,
            include_visual_fallback=include_visual_fallback,
        )
        total = len(paths)
        ctx.state.append_activity(
            f"DOCUMENT REMOVE | scope={scope!r} | include_visual_fallback={include_visual_fallback} | "
            f"matched_paths={total} | quarantine_under_data_dir"
        )
        ctx.state.update_job(
            job_id,
            f"Matched {total} document image(s); each file is copied to this Mac, then removed from the phone.",
            progress_current=0 if total else None,
            progress_total=total if total else None,
        )
        if total == 0:
            ctx.state.finish_job(job_id, "No matching document images for this scope.")
            ctx.state.document_last_ledger = {
                "batch_id": None,
                "removed_from_device": 0,
                "failed": [],
            }
            _touch(ctx)
            return
        def on_file(cur: int, tot: int, fname: str) -> None:
            ctx.state.update_job(
                job_id,
                f"Document copy+remove {cur}/{tot} | device_path={fname}",
                progress_current=cur,
                progress_total=tot,
            )

        res = documents.quarantine_and_remove(
            paths, root, ctx.settings.data_dir, batch_id=None, on_file=on_file
        )
        removed = int(res.get("copied_then_removed") or 0)
        failed = list(res.get("failed") or [])
        ctx.state.document_last_ledger = {
            "batch_id": res.get("batch_id"),
            "removed_from_device": removed,
            "failed": failed[:60],
            "quarantine_path": res.get("quarantine_dir"),
        }
        ctx.state.finish_job(
            job_id,
            f"Removed {removed}/{total} from device; undo available until you finalize or unmount.",
        )
        log_event(
            "document_remove_done",
            job_id=job_id,
            batch_id=res.get("batch_id"),
            removed=removed,
            failed=len(failed),
        )
    except Exception as e:
        ctx.state.last_error = str(e)
        ctx.state.finish_job(job_id, str(e))
        log_event("document_remove_error", job_id=job_id, error=str(e))
    _touch(ctx)


@router.get("/api/documents/preview")
def api_documents_preview(
    request: Request,
    scope: Literal["all", "older_than_90d"] = "older_than_90d",
    include_visual_fallback: bool = False,
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(None, ge=1, le=500),
) -> dict[str, Any]:
    ctx = _ctx(request)
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        raise HTTPException(400, "Mount the device first.")
    mroot = root.resolve()
    paths = documents.iter_document_paths(
        root,
        scope,
        include_visual_fallback=include_visual_fallback,
    )
    total_bytes = 0
    for p in paths:
        try:
            total_bytes += p.stat().st_size
        except OSError:
            continue
    page_size = limit if limit is not None else ctx.settings.preview_page_size
    page_size = max(1, min(500, page_size))
    thumb_relpaths: list[str] = []
    for p in paths:
        rel = _relpath_if_under(str(p.resolve()), mroot)
        if rel:
            thumb_relpaths.append(rel)
    total_thumbs = len(thumb_relpaths)
    page_thumbs = thumb_relpaths[offset : offset + page_size]
    sample = [str(p) for p in paths[:12]]
    return {
        "count": len(paths),
        "total_bytes": total_bytes,
        "sample": sample,
        "thumbnail_sample_relpaths": page_thumbs,
        "thumbnail_sample_total": total_thumbs,
        "offset": offset,
        "limit": page_size,
        "has_more": offset + len(page_thumbs) < total_thumbs,
        "scope": scope,
        "include_visual_fallback": include_visual_fallback,
    }


@router.get("/api/documents/finalize-preview")
def api_documents_finalize_preview(
    request: Request,
    all_batches: bool = False,
    batch_id: Optional[str] = None,
) -> dict[str, Any]:
    ctx = _ctx(request)
    bid = batch_id.strip() if batch_id else None
    if not all_batches and not bid:
        return documents.finalize_preview_data(ctx.settings.data_dir, batch_id=None, all_batches=False)
    out = documents.finalize_preview_data(
        ctx.settings.data_dir,
        batch_id=bid,
        all_batches=all_batches,
    )
    return out


@router.get("/api/documents/holding-thumbnail")
def api_documents_holding_thumbnail(
    request: Request,
    batch_id: str,
    stored: str,
) -> Any:
    ctx = _ctx(request)
    if not batch_id or ".." in batch_id or "/" in batch_id or "\\" in batch_id:
        raise HTTPException(400, "Invalid batch id.")
    if not stored or ".." in stored or "/" in stored or "\\" in stored:
        raise HTTPException(400, "Invalid stored name.")
    qroot = documents.document_quarantine_root(ctx.settings.data_dir).resolve()
    batch_dir = (qroot / batch_id).resolve()
    try:
        batch_dir.relative_to(qroot)
    except ValueError:
        raise HTTPException(400, "Invalid batch.")
    if not batch_dir.is_dir():
        raise HTTPException(404, "Batch not found.")
    full = (batch_dir / Path(stored).name).resolve()
    try:
        full.relative_to(batch_dir)
    except ValueError:
        raise HTTPException(400, "Invalid path.")
    if not full.is_file():
        raise HTTPException(404, "Not found.")
    ctx.thumb_semaphore.acquire()
    try:
        data = thumbnails.get_thumbnail_jpeg(
            full,
            batch_dir.resolve(),
            ctx.settings.thumbnail_cache_dir,
            ctx.settings.thumbnail_max_edge,
            ctx.settings.thumbnail_jpeg_quality,
            ctx.settings.thumbnail_cache_max_mb,
        )
    finally:
        ctx.thumb_semaphore.release()
    return Response(content=data, media_type="image/jpeg")


@router.post("/api/documents/remove")
def api_documents_remove(body: DocumentRemoveBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if body.confirm != "REMOVE_TAGGED_DOCUMENTS":
        raise HTTPException(400, "Confirmation phrase mismatch.")
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        raise HTTPException(400, "Mount the device first.")
    if ctx.state.phase == Phase.mounting:
        raise HTTPException(400, "Wait for mounting to finish before document removal.")
    if ctx.state.phase == Phase.scanning:
        raise HTTPException(400, "Wait for the duplicate scan to finish.")
    if ctx.state.phase == Phase.deleting:
        raise HTTPException(400, "Wait for the duplicate delete job to finish.")
    job = ctx.state.start_job("document_remove", "Removing document images…")
    log_event("document_remove_started", job_id=job.job_id, scope=body.scope)
    t = threading.Thread(
        target=_run_document_remove_thread,
        args=(ctx,),
        kwargs={
            "scope": body.scope,
            "include_visual_fallback": body.include_visual_fallback,
            "job_id": job.job_id,
        },
        daemon=True,
    )
    t.start()
    _touch(ctx)
    return {"ok": True, "message": "Document removal started in background."}


@router.post("/api/documents/undo")
def api_documents_undo(body: DocumentBatchBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        raise HTTPException(400, "Mount the device first (needed to copy files back).")
    bid = body.batch_id
    if not bid:
        batches = documents.list_batches(ctx.settings.data_dir)
        if not batches:
            raise HTTPException(400, "Nothing in the holding area to undo.")
        bid = str(batches[0]["batch_id"])
    res = documents.undo_batch(bid, root, ctx.settings.data_dir)
    log_event("document_undo", batch_id=bid, restored=len(res["restored"]), errors=len(res["errors"]))
    _touch(ctx)
    return {"ok": True, **res}


@router.post("/api/documents/finalize")
def api_documents_finalize(body: DocumentBatchBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if body.batch_id:
        out = documents.finalize_batch(body.batch_id, ctx.settings.data_dir)
    else:
        n = documents.finalize_all_batches(ctx.settings.data_dir)
        out = {"removed_all": True, "batches_cleared": n}
    log_event("document_finalize", batch_id=body.batch_id, detail=str(out))
    _touch(ctx)
    return {"ok": True, **out}


@router.get("/api/thumbnail")
def api_thumbnail(
    relpath: str,
    request: Request,
    max_edge: Optional[int] = Query(
        None,
        ge=64,
        le=2048,
        description="Optional longer edge for thumbnails (duplicate review uses a larger value).",
    ),
) -> Any:
    from fastapi.responses import Response

    ctx = _ctx(request)
    full, root = _resolve_thumbnail_file(ctx, relpath)
    edge = int(max_edge) if max_edge is not None else ctx.settings.thumbnail_max_edge
    ctx.thumb_semaphore.acquire()
    try:
        data = thumbnails.get_thumbnail_jpeg(
            full,
            root,
            ctx.settings.thumbnail_cache_dir,
            edge,
            ctx.settings.thumbnail_jpeg_quality,
            ctx.settings.thumbnail_cache_max_mb,
        )
    finally:
        ctx.thumb_semaphore.release()
    return Response(content=data, media_type="image/jpeg")


@router.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            ctx = _ctx(request)
            _sync_runtime(ctx)
            payload = _enriched_snapshot(ctx)
            payload["keep_mode"] = ctx.effective_keep_mode()
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(max(0.2, ctx.settings.sse_poll_interval_ms / 1000.0))

    return StreamingResponse(gen(), media_type="text/event-stream")
