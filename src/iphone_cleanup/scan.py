"""Duplicate scan: size buckets + perceptual hash grouping."""

from __future__ import annotations

import json
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image

from iphone_cleanup.auto_best import _exif_capture_ts

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp"}


class ScanCancelled(Exception):
    """Raised when the operator cancels mid-scan; carries completed raw groups."""

    def __init__(self, partial_groups: list[list[Path]]) -> None:
        self.partial_groups = partial_groups
        super().__init__("scan_cancelled")


def _walk_images(
    root: Path,
    cancel_event: threading.Event | None = None,
    *,
    cancel_every: int = 256,
) -> list[Path]:
    """Collect image paths under root; cooperatively cancel during large rglob walks."""
    out: list[Path] = []
    n = 0
    for p in root.rglob("*"):
        n += 1
        if cancel_event and cancel_every > 0 and n % cancel_every == 0 and cancel_event.is_set():
            raise ScanCancelled([])
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            out.append(p)
    return out


def _phash(
    path: Path,
    max_dim: int,
    cancel_check: Callable[[], None] | None = None,
) -> imagehash.ImageHash | None:
    try:
        if cancel_check:
            cancel_check()
        with Image.open(path) as im:
            if cancel_check:
                cancel_check()
            im = im.convert("RGB")
            if cancel_check:
                cancel_check()
            im.thumbnail((max_dim, max_dim))
            if cancel_check:
                cancel_check()
            return imagehash.phash(im)
    except Exception:
        return None


def _union_find(n: int, pairs: list[tuple[int, int]]) -> list[int]:
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)
    return [find(i) for i in range(n)]


def finalize_groups(raw_groups: list[list[Path]], *, scan_kind: str) -> list[dict[str, Any]]:
    out_groups: list[dict[str, Any]] = []
    for paths in raw_groups:
        total = 0
        for p in paths:
            try:
                total += Path(p).stat().st_size
            except OSError:
                pass
        largest = paths[0]
        largest_sz = -1
        for p in paths:
            try:
                s = Path(p).stat().st_size
            except OSError:
                s = 0
            if s > largest_sz:
                largest_sz = s
                largest = p
        largest_s = str(Path(largest).resolve())
        gid = "g_" + uuid.uuid4().hex[:10]
        out_groups.append(
            {
                "id": gid,
                "paths": [str(Path(x).resolve()) for x in paths],
                "bytesSavedIfOneKept": max(0, total - largest_sz),
                "recommendedKeep": largest_s,
                "recommendedKeeps": [largest_s],
                "scan_kind": scan_kind,
            }
        )
    return out_groups


def finalize_duplicate_groups(raw_groups: list[list[Path]]) -> list[dict[str, Any]]:
    return finalize_groups(raw_groups, scan_kind="exact")


def scan_duplicates(
    mount_root: Path,
    phash_threshold: int,
    phash_max_dim: int = 256,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    files = _walk_images(mount_root, cancel_event)
    raw_groups: list[list[Path]] = []
    processed = 0

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled(raw_groups)

    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in files:
        _check_cancel()
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        by_size[sz].append(p)

    for size, bucket in by_size.items():
        _check_cancel()
        if len(bucket) < 2:
            processed += len(bucket)
            if progress_callback:
                progress_callback(processed, len(files), f"Size {size}: skip")
            continue
        hashes: list[imagehash.ImageHash | None] = []
        for p in bucket:
            _check_cancel()
            hashes.append(_phash(p, phash_max_dim, cancel_check=_check_cancel))
            processed += 1
            if progress_callback and processed % 25 == 0:
                progress_callback(processed, len(files), f"Hashing {p.name}")
        idxs = [i for i, h in enumerate(hashes) if h is not None]
        pairs: list[tuple[int, int]] = []
        pair_ops = 0
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                pair_ops += 1
                if pair_ops % 128 == 0:
                    _check_cancel()
                i, j = idxs[ii], idxs[jj]
                hi, hj = hashes[i], hashes[j]
                if hi is None or hj is None:
                    continue
                if hi - hj <= phash_threshold:
                    pairs.append((i, j))
        _check_cancel()
        roots = _union_find(len(bucket), pairs)
        clusters: dict[int, list[int]] = defaultdict(list)
        for i, r in enumerate(roots):
            clusters[r].append(i)
        for members in clusters.values():
            if len(members) < 2:
                continue
            paths = [str(bucket[i].resolve()) for i in members]
            raw_groups.append([Path(x) for x in paths])

    return finalize_duplicate_groups(raw_groups)


def scan_fuzzy_roll_bursts(
    mount_root: Path,
    *,
    phash_max_dim: int,
    max_adjacent_hamming: int,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """
    Group consecutive photos in capture-time roll order when adjacent pHash distance is low
    (visually similar bursts, e.g. many shots of the same subject).
    """
    files = _walk_images(mount_root, cancel_event)
    files.sort(key=lambda p: (_exif_capture_ts(p), str(p)))
    n = len(files)
    completed_bursts: list[list[Path]] = []

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled(completed_bursts)

    hashes: list[imagehash.ImageHash | None] = []
    for i, p in enumerate(files):
        _check_cancel()
        hashes.append(_phash(p, phash_max_dim, cancel_check=_check_cancel))
        if progress_callback and (i + 1) % 25 == 0:
            progress_callback(i + 1, max(n, 1), f"Fuzzy hash {p.name}")

    i = 0
    while i < n:
        _check_cancel()
        j = i
        chain = [files[i]]
        while j + 1 < n:
            _check_cancel()
            hi, hj = hashes[j], hashes[j + 1]
            if hi is None or hj is None or (hi - hj) > max_adjacent_hamming:
                break
            j += 1
            chain.append(files[j])
        if len(chain) >= 2:
            completed_bursts.append([Path(x) for x in chain])
        i = j + 1

    return finalize_groups(completed_bursts, scan_kind="fuzzy")


def write_artifact(path: Path, groups: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sk = (groups[0].get("scan_kind") if groups else None) or "exact"
    payload = {"scan_kind": sk, "groups": groups}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_artifact(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("groups") or [])
