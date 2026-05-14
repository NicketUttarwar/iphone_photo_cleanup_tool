"""ifuse mount / unmount orchestration."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _tool(bin_path: str | None, default: str) -> str:
    return str(Path(bin_path)) if bin_path else default


def is_mountpoint(path: Path) -> bool:
    try:
        proc = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return False
        needle = str(path.resolve())
        for line in proc.stdout.splitlines():
            if needle in line and ("fuse" in line.lower() or "ifuse" in line.lower() or "osxfuse" in line.lower()):
                return True
        return False
    except OSError:
        return False


def mount_media(
    ifuse_bin: str | None,
    mount_point: Path,
    udid: str | None,
    *,
    status_callback: Callable[[str], None] | None = None,
    wait_seconds: float = 45.0,
    poll_interval: float = 0.25,
) -> tuple[bool, str, Any]:
    def emit(msg: str) -> None:
        if status_callback:
            status_callback(msg)

    mount_point.mkdir(parents=True, exist_ok=True)
    if is_mountpoint(mount_point):
        emit(
            f"Mount point {mount_point.resolve()} is already a FUSE/ifuse mount — skipping launch "
            f"(udid={udid or 'default'})."
        )
        return True, "Already mounted at this path.", None
    cmd = [_tool(ifuse_bin, "ifuse")]
    if udid:
        cmd.extend(["-u", udid])
    cmd.append(str(mount_point))
    emit(
        f"Preparing ifuse: mount_point={mount_point.resolve()} | udid={udid or '(ifuse default)'} | "
        f"command={' '.join(cmd)!r}"
    )
    emit("Launching ifuse subprocess — polling until the iPhone volume appears (keep phone unlocked on USB)…")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + float(wait_seconds)
    ticks = 0
    start_m = time.monotonic()
    while time.monotonic() < deadline:
        if is_mountpoint(mount_point):
            elapsed = time.monotonic() - start_m
            emit(
                f"Mount OK: volume visible at {mount_point.resolve()} after {elapsed:.2f}s "
                f"(ifuse pid={getattr(proc, 'pid', '?')})."
            )
            return True, "Mounted.", proc
        time.sleep(poll_interval)
        ticks += 1
        if ticks % 8 == 0:
            elapsed = time.monotonic() - start_m
            remain = max(0.0, deadline - time.monotonic())
            emit(
                f"Still waiting for FUSE/ifuse: elapsed={elapsed:.1f}s | about {remain:.1f}s left in window | "
                f"poll_ticks={ticks} — keep the iPhone unlocked and on USB."
            )
    proc.terminate()
    try:
        err = proc.stderr.read() if proc.stderr else ""
        out = proc.stdout.read() if proc.stdout else ""
    except Exception:
        err, out = "", ""
    msg = (err or out or "ifuse did not produce a mount within the wait window.").strip()
    emit(
        f"Mount FAILED after {time.monotonic() - start_m:.2f}s: ifuse did not expose the volume. "
        f"First stderr/stdout chunk (trimmed): {msg[:400]!r}"
    )
    return False, msg, None


def unmount_path(mount_point: Path) -> tuple[bool, str]:
    mp_path = mount_point.resolve()
    if not is_mountpoint(mp_path):
        return True, "Nothing is mounted at this path (already unmounted or mount never succeeded)."
    mp = str(mp_path)
    for args in (["diskutil", "unmount", mp], ["/sbin/umount", mp]):
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return True, (proc.stdout or "Unmounted.").strip()
    proc = subprocess.run(["diskutil", "unmount", "force", mp], capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        return True, (proc.stdout or "Force unmounted.").strip()
    return False, (proc.stderr or proc.stdout or "Unmount failed.").strip()
