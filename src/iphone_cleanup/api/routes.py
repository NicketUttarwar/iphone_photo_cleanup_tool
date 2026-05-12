"""HTTP API and HTML pages."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from iphone_cleanup import auto_best, delete as delmod, device_bridge, documents, mount, prefs, scan, thumbnails
from iphone_cleanup.app_context import AppCtx
from iphone_cleanup.app_log import log_event
from iphone_cleanup.state import Phase

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ctx(request: Request) -> AppCtx:
    return request.app.state.ctx


def _touch(ctx: AppCtx) -> None:
    ctx.state.next_event_seq()


def _enriched_snapshot(ctx: AppCtx) -> dict[str, Any]:
    snap = ctx.state.snapshot()
    snap["document_batches"] = documents.list_batches(ctx.settings.data_dir)
    return snap


class KeepModeBody(BaseModel):
    mode: Literal["manual", "auto_best"]
    apply_auto: bool = False


class SelectionBody(BaseModel):
    group_id: str
    keep_path: str


class DeleteBody(BaseModel):
    paths: list[str] = Field(default_factory=list)
    confirm: str


class DocumentRemoveBody(BaseModel):
    scope: Literal["all", "older_than_90d"]
    confirm: str
    include_visual_fallback: bool = False


class DocumentBatchBody(BaseModel):
    batch_id: Optional[str] = None


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
    snap = _enriched_snapshot(ctx)
    snap["keep_mode"] = ctx.effective_keep_mode()
    return snap


@router.get("/api/device")
def api_device(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    dev = device_bridge.detect_device(ctx.settings.ideviceinfo, ctx.settings.idevice_id)
    ctx.state.device_info = dev
    if dev.get("trusted") and ctx.state.phase == Phase.idle:
        ctx.state.set_phase(Phase.device_detected)
    _touch(ctx)
    return dev


@router.post("/api/mount")
def api_mount(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    dev = ctx.state.device_info or device_bridge.detect_device(ctx.settings.ideviceinfo, ctx.settings.idevice_id)
    if not dev.get("trusted"):
        raise HTTPException(400, dev.get("error") or "Device not ready for mount.")
    udid = dev.get("udid")
    mp = ctx.settings.mount_point
    ok, msg, proc = mount.mount_media(ctx.settings.ifuse, mp, udid)
    if not ok:
        ctx.state.last_error = msg
        _touch(ctx)
        log_event("mount_fail", detail=msg)
        raise HTTPException(500, msg)
    ctx.state.mount_path = mp.resolve()
    ctx.state.mount_udid = udid
    ctx.state.ifuse_proc = proc
    ctx.state.set_phase(Phase.mounted)
    ctx.state.last_error = ""
    _touch(ctx)
    log_event("mount_ok", udid=str(udid), mount_path=str(ctx.state.mount_path))
    return {"ok": True, "message": msg, "mount_path": str(ctx.state.mount_path)}


@router.post("/api/unmount")
def api_unmount(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    mp = ctx.settings.mount_point
    if mount.is_mountpoint(mp):
        ctx.state.set_phase(Phase.unmounting)
    ok, msg = mount.unmount_path(mp)
    if ok:
        ctx.state.mount_path = None
        ctx.state.mount_udid = None
        ctx.state.ifuse_proc = None
        ctx.state.set_phase(Phase.idle)
        ctx.state.last_error = ""
        n_batches = documents.finalize_all_batches(ctx.settings.data_dir)
        cleared = thumbnails.clear_jpeg_cache(ctx.settings.thumbnail_cache_dir)
        log_event("unmount_finalize", quarantine_batches_dropped=n_batches, thumbnail_cache_files_cleared=cleared)
    else:
        ctx.state.last_error = msg
        if mount.is_mountpoint(mp):
            ctx.state.set_phase(Phase.mounted)
        else:
            ctx.state.mount_path = None
            ctx.state.mount_udid = None
            ctx.state.ifuse_proc = None
            ctx.state.set_phase(Phase.idle)
    _touch(ctx)
    log_event("unmount", ok=ok, detail=msg)
    return {"ok": ok, "message": msg}


def _apply_auto_groups(ctx: AppCtx, *, force: bool = False) -> None:
    root = ctx.state.mount_path
    if not root:
        return
    face = ctx.settings.duplicates_auto_face_eye and (
        ctx.effective_keep_mode() == "auto_best" or force
    )
    max_img = ctx.settings.duplicates_face_eye_max_images
    for g in ctx.state.duplicate_groups:
        keep = auto_best.pick_recommended(
            list(g.get("paths") or []),
            face_eye=face,
            face_eye_max_images=max_img,
        )
        if keep:
            g["recommendedKeep"] = keep
            ctx.state.group_keep[str(g["id"])] = keep
    _touch(ctx)


def _run_scan_thread(ctx: AppCtx) -> None:
    job = ctx.state.start_job("scan", "Scanning library for duplicates…")
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        ctx.state.finish_job(job.job_id, "No mount path.")
        ctx.state.set_phase(Phase.mounted)
        _touch(ctx)
        return
    cancelled = False
    groups: list[dict[str, Any]]

    def prog(cur: int, total: int, msg: str) -> None:
        ctx.state.update_job(job.job_id, f"{msg} ({cur}/{total})")

    try:
        groups = scan.scan_duplicates(
            root,
            ctx.settings.phash_threshold,
            progress_callback=prog,
            cancel_event=ctx.state.scan_cancel_event,
        )
    except scan.ScanCancelled as e:
        groups = scan.finalize_duplicate_groups(e.partial_groups)
        cancelled = True
    except Exception as e:
        ctx.state.last_error = str(e)
        ctx.state.finish_job(job.job_id, str(e))
        ctx.state.set_phase(Phase.mounted)
        _touch(ctx)
        log_event("scan_error", job_id=job.job_id, error=str(e))
        return

    ctx.state.duplicate_groups = groups
    ctx.state.group_keep = {}
    for g in groups:
        gid = str(g["id"])
        rk = str(g.get("recommendedKeep") or (g.get("paths") or [""])[0])
        g["recommendedKeep"] = rk
        ctx.state.group_keep[gid] = rk
    if ctx.effective_keep_mode() == "auto_best":
        _apply_auto_groups(ctx, force=False)
    art = ctx.settings.scan_artifacts_dir / f"scan_{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
    scan.write_artifact(art, groups)
    ctx.state.scan_artifact_path = art
    if cancelled and len(groups) == 0:
        ctx.state.set_phase(Phase.mounted)
        ctx.state.finish_job(job.job_id, "Scan cancelled (no groups yet).")
        log_event("scan_cancelled_empty", job_id=job.job_id)
    else:
        ctx.state.set_phase(Phase.reviewing)
        if cancelled:
            ctx.state.finish_job(job.job_id, f"Scan cancelled — {len(groups)} duplicate group(s) so far.")
            log_event("scan_cancelled", job_id=job.job_id, groups=len(groups))
        else:
            ctx.state.finish_job(job.job_id, f"Found {len(groups)} duplicate groups.")
            log_event("scan_done", job_id=job.job_id, groups=len(groups))
    _touch(ctx)


@router.post("/api/scan/start")
def api_scan_start(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if ctx.state.phase not in (Phase.mounted, Phase.reviewing):
        raise HTTPException(400, "Mount the device before scanning.")
    ctx.state.scan_cancel_event.clear()
    ctx.state.set_phase(Phase.scanning)
    log_event("scan_start")
    t = threading.Thread(target=_run_scan_thread, args=(ctx,), daemon=True)
    t.start()
    _touch(ctx)
    return {"ok": True, "message": "Scan started in background."}


@router.post("/api/scan/cancel")
def api_scan_cancel(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if ctx.state.phase != Phase.scanning:
        raise HTTPException(400, "No scan is running.")
    ctx.state.scan_cancel_event.set()
    log_event("scan_cancel_requested")
    _touch(ctx)
    return {"ok": True, "message": "Cancellation requested; wait for the scan job to wind down."}


@router.get("/api/scan/groups")
def api_scan_groups(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    return {"groups": ctx.state.duplicate_groups, "keep": ctx.state.group_keep, "keep_mode": ctx.effective_keep_mode()}


@router.post("/api/keep-mode")
def api_keep_mode(body: KeepModeBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    ctx.state.runtime_keep_mode = body.mode
    prefs.save_keep_mode(ctx.settings, body.mode)
    if body.mode == "auto_best":
        _apply_auto_groups(ctx, force=False)
    elif body.apply_auto:
        _apply_auto_groups(ctx, force=True)
    _touch(ctx)
    return {"ok": True, "keep_mode": ctx.effective_keep_mode()}


@router.post("/api/selection")
def api_selection(body: SelectionBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    gid = body.group_id
    keep = body.keep_path
    found = False
    for g in ctx.state.duplicate_groups:
        if str(g.get("id")) != gid:
            continue
        paths = list(g.get("paths") or [])
        if keep not in paths:
            raise HTTPException(400, "keep_path must be one of the group paths.")
        ctx.state.group_keep[gid] = keep
        found = True
        break
    if not found:
        raise HTTPException(404, "Unknown group.")
    _touch(ctx)
    return {"ok": True}


@router.post("/api/delete")
def api_delete(body: DeleteBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if body.confirm != "DELETE_SELECTED_FILES":
        raise HTTPException(400, "Confirmation phrase mismatch.")
    root = ctx.state.mount_path
    if not root:
        raise HTTPException(400, "Not mounted.")
    job = ctx.state.start_job("delete", "Deleting selected duplicates…")
    log_event("delete_job_started", job_id=job.job_id)
    ctx.state.set_phase(Phase.deleting)

    def work() -> None:
        try:
            to_delete: list[str] = []
            for g in ctx.state.duplicate_groups:
                gid = str(g.get("id"))
                keep = ctx.state.group_keep.get(gid)
                if not keep:
                    keep = str(g.get("recommendedKeep") or "")
                for p in g.get("paths") or []:
                    if str(p) != str(keep):
                        to_delete.append(str(p))
            extra = [p for p in body.paths if p not in to_delete]
            if extra:
                ctx.state.update_job(job.job_id, "Extra paths ignored (not in duplicate-to-delete set).")

            def prog(done: int, total: int, del_n: int, fail_n: int, skip_n: int) -> None:
                ctx.state.update_job(
                    job.job_id,
                    f"Deleting {done}/{total} files… (deleted={del_n} failed={fail_n} skipped={skip_n})",
                )

            res = delmod.delete_paths_chunked(
                to_delete,
                root,
                ctx.settings.delete_chunk_size,
                on_progress=prog,
            )
            ctx.state.last_delete_ledger = {
                "deleted_count": len(res["deleted"]),
                "failed_count": len(res["failed"]),
                "skipped_count": len(res["skipped"]),
                "failed_sample": res["failed"][:40],
            }
            ctx.state.finish_job(
                job.job_id,
                f"Deleted {len(res['deleted'])} files; failed {len(res['failed'])}; skipped {len(res['skipped'])}.",
            )
            ctx.state.set_phase(Phase.reviewing)
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
            ctx.state.set_phase(Phase.reviewing)
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
        ctx.state.update_job(job_id, f"Matched {total} image(s); copying to Mac holding area, then removing from device…")
        if total == 0:
            ctx.state.finish_job(job_id, "No matching document images for this scope.")
            ctx.state.document_last_ledger = {
                "batch_id": None,
                "removed_from_device": 0,
                "failed": [],
            }
            _touch(ctx)
            return
        res = documents.quarantine_and_remove(paths, root, ctx.settings.data_dir, batch_id=None)
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
) -> dict[str, Any]:
    ctx = _ctx(request)
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        raise HTTPException(400, "Mount the device first.")
    paths = documents.iter_document_paths(
        root,
        scope,
        include_visual_fallback=include_visual_fallback,
    )
    sample = [str(p) for p in paths[:30]]
    return {"count": len(paths), "sample": sample, "scope": scope, "include_visual_fallback": include_visual_fallback}


@router.post("/api/documents/remove")
def api_documents_remove(body: DocumentRemoveBody, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    if body.confirm != "REMOVE_TAGGED_DOCUMENTS":
        raise HTTPException(400, "Confirmation phrase mismatch.")
    root = ctx.state.mount_path
    if not root or not root.is_dir():
        raise HTTPException(400, "Mount the device first.")
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
def api_thumbnail(relpath: str, request: Request) -> Any:
    from fastapi.responses import Response

    ctx = _ctx(request)
    root = ctx.state.mount_path
    if not root:
        raise HTTPException(400, "Not mounted.")
    if ".." in Path(relpath).parts:
        raise HTTPException(400, "Invalid path.")
    full = (root / relpath).resolve()
    try:
        full.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(400, "Outside mount.")
    if not full.is_file():
        raise HTTPException(404, "Not found.")
    ctx.thumb_semaphore.acquire()
    try:
        data = thumbnails.get_thumbnail_jpeg(
            full,
            root.resolve(),
            ctx.settings.thumbnail_cache_dir,
            ctx.settings.thumbnail_max_edge,
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
            payload = _enriched_snapshot(ctx)
            payload["keep_mode"] = ctx.effective_keep_mode()
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(max(0.2, ctx.settings.sse_poll_interval_ms / 1000.0))

    return StreamingResponse(gen(), media_type="text/event-stream")
