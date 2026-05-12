"""Duplicate scan: size buckets + perceptual hash grouping."""

from __future__ import annotations

import json
import threading
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image

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


def _walk_images(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            out.append(p)
    return out


def _phash(path: Path, max_dim: int) -> imagehash.ImageHash | None:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_dim, max_dim))
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


def finalize_duplicate_groups(raw_groups: list[list[Path]]) -> list[dict[str, Any]]:
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
        gid = "g_" + uuid.uuid4().hex[:10]
        out_groups.append(
            {
                "id": gid,
                "paths": [str(Path(x).resolve()) for x in paths],
                "bytesSavedIfOneKept": max(0, total - largest_sz),
                "recommendedKeep": str(Path(largest).resolve()),
            }
        )
    return out_groups


def scan_duplicates(
    mount_root: Path,
    phash_threshold: int,
    phash_max_dim: int = 256,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    files = _walk_images(mount_root)
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in files:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        by_size[sz].append(p)

    raw_groups: list[list[Path]] = []
    processed = 0

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled(raw_groups)

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
            hashes.append(_phash(p, phash_max_dim))
            processed += 1
            if progress_callback and processed % 25 == 0:
                progress_callback(processed, len(files), f"Hashing {p.name}")
        idxs = [i for i, h in enumerate(hashes) if h is not None]
        pairs: list[tuple[int, int]] = []
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                i, j = idxs[ii], idxs[jj]
                hi, hj = hashes[i], hashes[j]
                if hi is None or hj is None:
                    continue
                if hi - hj <= phash_threshold:
                    pairs.append((i, j))
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


def write_artifact(path: Path, groups: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"groups": groups}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_artifact(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("groups") or [])
