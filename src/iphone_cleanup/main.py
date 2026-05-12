"""FastAPI application factory."""

from __future__ import annotations

import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from iphone_cleanup import mount, prefs
from iphone_cleanup.api.routes import router
from iphone_cleanup.app_context import AppCtx
from iphone_cleanup.app_log import log_event, setup_file_logging
from iphone_cleanup.state import Phase


def create_app(ctx: AppCtx) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx.settings.data_dir.mkdir(parents=True, exist_ok=True)
        ctx.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        ctx.settings.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)
        ctx.settings.scan_artifacts_dir.mkdir(parents=True, exist_ok=True)
        ctx.settings.mount_point.parent.mkdir(parents=True, exist_ok=True)
        setup_file_logging(ctx.settings.logs_dir, ctx.settings.log_level)
        log_event("server_start", host=ctx.settings.server_host, port=ctx.settings.server_port)
        saved = prefs.load_ui_state(ctx.settings).get("keep_mode")
        if saved in ("manual", "auto_best"):
            ctx.state.runtime_keep_mode = saved
        url = f"http://{ctx.settings.server_host}:{ctx.settings.server_port}/"
        if ctx.settings.ui_open_browser and not ctx.no_open_browser:

            def open_browser() -> None:
                time.sleep(0.8)
                webbrowser.open(url)

            threading.Thread(target=open_browser, daemon=True).start()
        try:
            yield
        finally:
            _shutdown(ctx)

    app = FastAPI(title="iPhone Photo Cleanup", lifespan=lifespan)
    app.state.ctx = ctx
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(router)
    return app


def _shutdown(ctx: AppCtx) -> None:
    """Best-effort cleanup invoked when the FastAPI lifespan ends.

    Runs whether the server stopped via SIGINT/SIGTERM or an exception, so
    that we never leave the iPhone FUSE mount or ifuse subprocess dangling.
    """
    try:
        ctx.state.scan_cancel_event.set()
    except Exception:
        pass

    mount_path = ctx.state.mount_path or ctx.settings.mount_point
    try:
        if mount_path and mount.is_mountpoint(mount_path):
            ctx.state.set_phase(Phase.unmounting)
            ok, msg = mount.unmount_path(mount_path)
            log_event("server_stop_unmount", ok=ok, message=msg, mount_path=str(mount_path))
        else:
            log_event("server_stop_unmount_skipped", mount_path=str(mount_path) if mount_path else None)
    except Exception as exc:
        log_event("server_stop_unmount_error", error=repr(exc))

    proc = ctx.state.ifuse_proc
    if proc is not None:
        try:
            if getattr(proc, "poll", lambda: 0)() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except Exception as exc:
            log_event("server_stop_ifuse_proc_error", error=repr(exc))

    ctx.state.ifuse_proc = None
    ctx.state.mount_path = None
    ctx.state.mount_udid = None
    ctx.state.set_phase(Phase.idle)
    log_event("server_stop")
