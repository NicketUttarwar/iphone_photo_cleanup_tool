"""Restore USB / mount state from the host and keep in-memory phase aligned with disk."""

from __future__ import annotations

import threading

from iphone_cleanup import device_bridge, mount, scan, scan_sessions
from iphone_cleanup.app_context import AppCtx
from iphone_cleanup.runtime_session import clear_runtime
from iphone_cleanup.state import Phase

_BUSY_PHASES = frozenset(
    {Phase.mounting, Phase.scanning, Phase.deleting, Phase.unmounting}
)


def sync_device(ctx: AppCtx, *, log_check: bool = False) -> dict[str, object]:
    """Detect the iPhone over USB and update ``device_info`` / idle phases."""
    dev = device_bridge.detect_device(ctx.settings.ideviceinfo, ctx.settings.idevice_id)
    with ctx.state.lock:
        ctx.state.device_info = dev
        phase = ctx.state.phase

    if log_check:
        trusted = bool(dev.get("trusted"))
        name = dev.get("name") or "(unknown)"
        err = dev.get("error")
        detail = f"name={name!r}" if trusted else f"error={err!r}"
        ctx.state.append_activity(f"DEVICE CHECK | trusted={trusted} | {detail}")

    if not dev.get("trusted"):
        if phase in (Phase.idle, Phase.device_detected):
            ctx.state.set_phase(Phase.idle)
        return dev

    if phase == Phase.idle:
        ctx.state.set_phase(Phase.device_detected)
    return dev


def sync_mount_from_disk(ctx: AppCtx) -> None:
    """Align mount_path and phase with whether the configured mount point is live."""
    mp = ctx.settings.mount_point
    mounted = mount.is_mountpoint(mp)

    with ctx.state.lock:
        phase = ctx.state.phase
        mem_path = ctx.state.mount_path
        had_ifuse = ctx.state.ifuse_proc is not None

    if phase in _BUSY_PHASES:
        if mounted:
            resolved = mp.resolve()
            with ctx.state.lock:
                ctx.state.mount_path = resolved
                if not ctx.state.mount_udid and ctx.state.device_info:
                    udid = ctx.state.device_info.get("udid")
                    if udid:
                        ctx.state.mount_udid = str(udid)
        elif mem_path is not None:
            try:
                if mem_path.resolve().is_dir():
                    return
            except OSError:
                return
        return

    if mounted:
        resolved = mp.resolve()
        with ctx.state.lock:
            ctx.state.mount_path = resolved
            if not ctx.state.mount_udid and ctx.state.device_info:
                udid = ctx.state.device_info.get("udid")
                if udid:
                    ctx.state.mount_udid = str(udid)

        if phase in (Phase.idle, Phase.device_detected, Phase.mounting):
            if ctx.state.duplicate_groups:
                ctx.state.set_phase(Phase.reviewing)
            else:
                ctx.state.set_phase(Phase.mounted)
        return

    if phase == Phase.mounting:
        return

    with ctx.state.lock:
        ctx.state.mount_path = None
        ctx.state.ifuse_proc = None

    # Short-circuit / test mounts may have mount_path without a live ifuse proc.
    if mem_path is not None and not had_ifuse and phase in (
        Phase.mounted,
        Phase.reviewing,
    ):
        with ctx.state.lock:
            ctx.state.mount_path = mem_path
        return

    if phase in (Phase.mounted, Phase.reviewing):
        trusted = bool(ctx.state.device_info and ctx.state.device_info.get("trusted"))
        ctx.state.set_phase(Phase.device_detected if trusted else Phase.idle)
    elif phase == Phase.unmounting:
        ctx.state.set_phase(Phase.idle)


def prepare_fresh_scan_run(ctx: AppCtx) -> None:
    """Drop saved scans and fuzzy-roll caches so each app run starts from a clean slate."""
    n_sessions = scan_sessions.clear_all_sessions(ctx.settings.user_scans_dir)
    n_cache = scan.clear_fuzzy_roll_cache(ctx.settings.scan_artifacts_dir)
    scan_sessions.reset_scan_state(ctx)
    clear_runtime(ctx.settings)
    ctx.state.append_activity(
        f"SESSION | fresh run — cleared {n_sessions} saved scan(s) and {n_cache} fuzzy cache file(s)"
    )


def schedule_fresh_scans_if_mounted(ctx: AppCtx) -> bool:
    """Start exact duplicate scan when mounted with no results yet; fuzzy scan chains after exact."""
    if not ctx.auto_scan_on_mount:
        return False
    mp = ctx.state.mount_path
    if not mp or not mp.is_dir():
        return False
    with ctx.state.lock:
        if ctx.state.duplicate_groups:
            return False
        phase = ctx.state.phase
    if phase in _BUSY_PHASES:
        return False
    if phase not in (Phase.mounted, Phase.reviewing):
        return False

    from iphone_cleanup.api.routes import _run_scan_thread, _touch

    ctx.state.scan_cancel_event.clear()
    with ctx.state.lock:
        ctx.state.scan_cancel_requested = False
        ctx.state.pending_rescan_kind = "fuzzy"
        ctx.state.scan_running_kind = "exact"
    ctx.state.set_phase(Phase.scanning, detail="exact duplicate scan")
    ctx.state.append_activity(
        "SESSION | device mounted — starting fresh exact scan (fuzzy roll scan queued next)"
    )
    t = threading.Thread(target=_run_scan_thread, args=(ctx, "exact", False), daemon=True)
    t.start()
    _touch(ctx)
    return True


def bootstrap_runtime(ctx: AppCtx) -> None:
    """On server start: rediscover device + mount without starting new background jobs."""
    ctx.state.append_activity("SESSION | restoring device and mount state from host…")
    sync_device(ctx)
    sync_mount_from_disk(ctx)
    if ctx.state.mount_path:
        ctx.state.append_activity(f"SESSION | existing mount at {ctx.state.mount_path}")
    elif ctx.state.device_info and ctx.state.device_info.get("trusted"):
        ctx.state.append_activity("SESSION | device trusted — mount when ready.")
