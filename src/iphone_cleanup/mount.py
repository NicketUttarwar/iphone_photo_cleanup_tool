"""ifuse mount / unmount orchestration."""

from __future__ import annotations

import subprocess
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
) -> tuple[bool, str, Any]:
    mount_point.mkdir(parents=True, exist_ok=True)
    if is_mountpoint(mount_point):
        return True, "Already mounted at this path.", None
    cmd = [_tool(ifuse_bin, "ifuse")]
    if udid:
        cmd.extend(["-u", udid])
    cmd.append(str(mount_point))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    import time

    time.sleep(0.8)
    if is_mountpoint(mount_point):
        return True, "Mounted.", proc
    proc.terminate()
    try:
        err = proc.stderr.read() if proc.stderr else ""
        out = proc.stdout.read() if proc.stdout else ""
    except Exception:
        err, out = "", ""
    msg = (err or out or "ifuse did not produce a mount.").strip()
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
