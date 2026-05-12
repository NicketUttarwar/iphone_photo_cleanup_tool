"""Wrap ideviceinfo / idevice_id for device detection (no iTunes)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _tool(settings_path: str | None, name: str) -> str:
    if settings_path:
        return str(Path(settings_path))
    return name


def _udid_from_idevice_id_line(line: str) -> str | None:
    """Extract UDID from idevice_id output.

    `idevice_id -ln` prints ``<udid> (USB)`` or ``<udid> (Network)`` per device.
    Passing the full line to ``ideviceinfo -u`` breaks detection; use the first token.
    """
    part = line.strip()
    if not part:
        return None
    return part.split()[0] or None


def list_udids(idevice_id_bin: str | None) -> list[str]:
    # `-l` = USB only (this app is wired-first). Avoid `-ln` suffixes unless we parse them.
    cmd = [_tool(idevice_id_bin, "idevice_id"), "-l"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for ln in proc.stdout.splitlines():
        udid = _udid_from_idevice_id_line(ln)
        if udid:
            out.append(udid)
    return out


def ideviceinfo_dict(ideviceinfo_bin: str | None, udid: str | None) -> tuple[dict[str, Any], str]:
    cmd = [_tool(ideviceinfo_bin, "ideviceinfo")]
    if udid:
        cmd.extend(["-u", udid])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        bin_name = cmd[0]
        return (
            {},
            f"Missing `{bin_name}`. Install libimobiledevice (Homebrew: "
            f"`brew install libimobiledevice`) or set `tools.ideviceinfo` in your YAML to the full path.",
        )
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        msg = err or proc.stdout or "ideviceinfo failed"
        if "PasswordProtected" in msg or "Pair" in msg or "trust" in msg.lower():
            return {}, "Device locked or not trusted — unlock iPhone and tap Trust."
        if "No device found" in msg or "ERROR: No device" in msg:
            return {}, "No device found. Connect USB, unlock, and trust this computer."
        return {}, msg
    info: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        if ": " not in line:
            continue
        k, v = line.split(": ", 1)
        info[k.strip()] = v.strip()
    return info, ""


def detect_device(ideviceinfo_bin: str | None, idevice_id_bin: str | None) -> dict[str, Any]:
    udids = list_udids(idevice_id_bin)
    if not udids:
        info, err = ideviceinfo_dict(ideviceinfo_bin, None)
        if err:
            return {"trusted": False, "udid": None, "name": None, "ios_version": None, "error": err}
        udid = info.get("UniqueDeviceID")
    else:
        udid = udids[0]
    info, err = ideviceinfo_dict(ideviceinfo_bin, udid)
    if err:
        return {"trusted": False, "udid": udid, "name": None, "ios_version": None, "error": err}
    return {
        "trusted": True,
        "udid": udid,
        "name": info.get("DeviceName"),
        "ios_version": info.get("ProductVersion"),
        "error": None,
        "raw": info,
    }
