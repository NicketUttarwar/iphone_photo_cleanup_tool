"""CLI entry for `python -m iphone_cleanup` (invoked by scripts/run.sh)."""

from __future__ import annotations

import argparse
import errno
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from iphone_cleanup.app_context import AppCtx
from iphone_cleanup.main import create_app
from iphone_cleanup.settings import Settings, load_merged_settings
from iphone_cleanup.state import AppState


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="iPhone Photo Cleanup local web app")
    p.add_argument("--repo-root", required=True, help="Repository root for relative paths in config")
    p.add_argument("--defaults-config", required=True, help="Path to app.defaults.yaml")
    p.add_argument("--local-config", default=None, help="Optional path to app.local.yaml")
    p.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not open a browser tab after the server starts",
    )
    return p.parse_args()


def _exit_if_listen_address_in_use(host: str, port: int) -> None:
    """Fail early with a clear hint when the server port is already taken."""
    for res in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        fam, socktype, proto, _canon, sockaddr = res
        try:
            with socket.socket(fam, socktype, proto) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(sockaddr)
            return
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
            print(
                f"Port {port} on {host!r} is already in use (another process is listening).\n"
                "Stop the other copy of this app, or pick another port in config "
                "(e.g. `server.port` in `config/app.local.yaml`).\n"
                f"To see what is using the port: lsof -nP -iTCP:{port} -sTCP:LISTEN",
                file=sys.stderr,
            )
            raise SystemExit(1) from e


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    defaults_path = Path(args.defaults_config).resolve()
    local_path = Path(args.local_config).resolve() if args.local_config else None
    merged = load_merged_settings(defaults_path, local_path)
    settings = Settings.from_dict(repo_root, merged)
    thumb_sem = threading.BoundedSemaphore(max(1, settings.max_concurrent_thumbnails))
    ctx = AppCtx(
        settings=settings,
        state=AppState(),
        no_open_browser=bool(args.no_open_browser),
        thumb_semaphore=thumb_sem,
    )
    app = create_app(ctx)
    _exit_if_listen_address_in_use(settings.server_host, settings.server_port)
    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
