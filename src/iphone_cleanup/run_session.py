"""One live session per ``scripts/run.sh`` execution (browser refresh stays in-session)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from iphone_cleanup.app_context import AppCtx

_SESSION_FILE = ".run_session.json"
_ENV_RUN_ID = "IPHONE_CLEANUP_RUN_ID"


def session_file(settings: Any) -> Path:
    return settings.data_dir / _SESSION_FILE


def read_session_file(settings: Any) -> dict[str, Any] | None:
    path = session_file(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def resolve_run_id(settings: Any) -> str:
    env_id = os.environ.get(_ENV_RUN_ID, "").strip()
    if env_id:
        return env_id
    data = read_session_file(settings)
    if data:
        rid = str(data.get("run_id") or "").strip()
        if rid:
            return rid
    return f"orphan_{int(time.time())}_{os.getpid()}"


def begin_run_session(ctx: AppCtx) -> str:
    """Claim this server process as the active run.sh session; drop stale disk state."""
    from iphone_cleanup.runtime_session import clear_runtime

    run_id = resolve_run_id(ctx.settings)
    ctx.run_session_id = run_id
    file_data = read_session_file(ctx.settings)
    file_run_id = str(file_data.get("run_id") or "") if file_data else ""
    shell_pid = int(file_data.get("shell_pid") or 0) if file_data else 0

    if file_run_id and file_run_id != run_id:
        clear_runtime(ctx.settings)
        ctx.state.append_activity(
            f"SESSION | new run {run_id!r} — cleared stale state from previous run {file_run_id!r}"
        )
    elif file_run_id == run_id and shell_pid and not _pid_alive(shell_pid):
        clear_runtime(ctx.settings)
        ctx.state.append_activity(
            f"SESSION | run {run_id!r} — previous shell ended; starting fresh in-memory state"
        )
    elif not file_run_id:
        ctx.state.append_activity(f"SESSION | started run {run_id!r}")

    payload = {
        "run_id": run_id,
        "server_pid": os.getpid(),
        "shell_pid": shell_pid or None,
        "started_at": time.time(),
    }
    path = session_file(ctx.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return run_id


def is_run_session_active(ctx: AppCtx) -> bool:
    if not ctx.run_session_id:
        return False
    data = read_session_file(ctx.settings)
    if not data:
        return False
    if str(data.get("run_id") or "") != ctx.run_session_id:
        return False
    shell_pid = int(data.get("shell_pid") or 0)
    if shell_pid and not _pid_alive(shell_pid):
        return False
    return True


def end_run_session(ctx: AppCtx) -> None:
    from iphone_cleanup.runtime_session import clear_runtime

    data = read_session_file(ctx.settings)
    if data and str(data.get("run_id") or "") == ctx.run_session_id:
        try:
            session_file(ctx.settings).unlink(missing_ok=True)
        except OSError:
            pass
    clear_runtime(ctx.settings)
    ctx.state.append_activity(f"SESSION | run {ctx.run_session_id!r} ended — server shutting down")
