#!/usr/bin/env python3
"""Mount the iPhone over USB and copy all Camera Roll photos to aii_iphone_photos/."""

from __future__ import annotations

import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from iphone_cleanup import device_bridge, mount  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "aii_iphone_photos"
MOUNT_POINT = REPO_ROOT / "data" / "iphone_mount"
PROGRESS_EVERY = 50
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp"}


def _effective_dcim_scan_root(mount_root: Path) -> Path:
    dcim = mount_root / "DCIM"
    if dcim.is_dir():
        return dcim.resolve()
    return mount_root.resolve()


def _log(msg: str) -> None:
    print(msg, flush=True)


def _collect_images(mount_root: Path) -> list[Path]:
    scan_root = _effective_dcim_scan_root(mount_root)
    images: list[Path] = []
    for path in scan_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            images.append(path)
    return sorted(images)


def _flat_dest_map(sources: list[Path]) -> dict[Path, Path]:
    """Map each source to a flat path under OUTPUT_DIR (no subfolders)."""
    by_name: dict[str, list[Path]] = defaultdict(list)
    for source in sources:
        by_name[source.name.lower()].append(source)

    mapping: dict[Path, Path] = {}
    for group in by_name.values():
        if len(group) == 1:
            mapping[group[0]] = OUTPUT_DIR / group[0].name
            continue
        for source in sorted(group, key=lambda p: str(p)):
            mapping[source] = OUTPUT_DIR / f"{source.parent.name}_{source.name}"
    return mapping


def main() -> int:
    _log(f"Output folder: {OUTPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    device = device_bridge.detect_device(None, None)
    if not device.get("trusted"):
        _log(f"ERROR: {device.get('error') or 'iPhone not available'}")
        _log("Connect via USB, unlock the phone, and tap Trust on the device.")
        return 1

    udid = device.get("udid")
    name = device.get("name") or "iPhone"
    _log(f"Found {name} ({udid})")

    ok, msg, proc = mount.mount_media(None, MOUNT_POINT, udid, status_callback=_log)
    if not ok:
        _log(f"ERROR: mount failed — {msg}")
        return 1

    copied = 0
    skipped = 0
    errors = 0
    start = time.monotonic()

    try:
        images = _collect_images(MOUNT_POINT)
        total = len(images)
        dest_map = _flat_dest_map(images)
        _log(f"Found {total} photo(s) under { _effective_dcim_scan_root(MOUNT_POINT) }")

        for idx, source in enumerate(images, start=1):
            dest = dest_map[source]
            if dest.exists() and dest.stat().st_size == source.stat().st_size:
                skipped += 1
            else:
                try:
                    shutil.copy2(source, dest)
                    copied += 1
                except OSError as exc:
                    errors += 1
                    _log(f"ERROR copying {source}: {exc}")

            if idx % PROGRESS_EVERY == 0 or idx == total:
                _log(f"Progress: {idx}/{total} (copied={copied}, skipped={skipped}, errors={errors})")
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        unmounted, unmount_msg = mount.unmount_path(MOUNT_POINT)
        if unmounted:
            _log(f"Unmounted iPhone ({unmount_msg})")
        else:
            _log(f"WARNING: unmount issue — {unmount_msg}")

    elapsed = time.monotonic() - start
    _log(
        f"Done in {elapsed:.1f}s — copied {copied}, skipped {skipped} (already present), "
        f"errors {errors}. Photos are in {OUTPUT_DIR}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
