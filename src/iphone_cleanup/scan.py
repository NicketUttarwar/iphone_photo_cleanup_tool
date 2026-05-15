"""Duplicate scan: size buckets + perceptual hash grouping."""

from __future__ import annotations

import hashlib
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

# iPhone often stores the same capture as both HEIF (original) and a JPEG companion; keep HEIF only.
_RAW_CAPTURE_EXTS = frozenset({".heic", ".heif"})
_JPEG_DERIVED_EXTS = frozenset({".jpg", ".jpeg"})


def _effective_dcim_scan_root(mount_root: Path) -> Path:
    """Limit duplicate/fuzzy scans to Camera Roll (DCIM) when that folder exists on the mount."""
    dcim = mount_root / "DCIM"
    if dcim.is_dir():
        return dcim.resolve()
    return mount_root.resolve()


def _dedupe_heif_jpeg_pairs(paths: list[Path]) -> list[Path]:
    """
    Drop JPEG companions when the same basename exists as HEIF in the same directory.

    This avoids treating Apple's paired storage as fuzzy-roll or exact-scan duplicates.
    """
    if len(paths) < 2:
        return paths
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for p in paths:
        key = (str(p.resolve().parent), p.stem.lower())
        groups[key].append(p)

    out: list[Path] = []
    for bucket in groups.values():
        if len(bucket) == 1:
            out.extend(bucket)
            continue
        by_ext: dict[str, list[Path]] = defaultdict(list)
        for p in bucket:
            by_ext[p.suffix.lower()].append(p)

        raw_paths: list[Path] = []
        for ext in _RAW_CAPTURE_EXTS:
            raw_paths.extend(by_ext.get(ext, []))
        jpeg_paths: list[Path] = []
        for ext in _JPEG_DERIVED_EXTS:
            jpeg_paths.extend(by_ext.get(ext, []))

        if raw_paths and jpeg_paths:
            def _sz(pa: Path) -> int:
                try:
                    return pa.stat().st_size
                except OSError:
                    return 0

            raw_paths.sort(key=_sz, reverse=True)
            out.append(raw_paths[0])
            continue

        out.extend(bucket)

    return out


class ScanCancelled(Exception):
    """Raised when the operator cancels mid-scan; carries completed raw groups."""

    def __init__(self, partial_groups: list[list[Path]]) -> None:
        self.partial_groups = partial_groups
        super().__init__("scan_cancelled")


def _rel_under_mount(root: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return p.name


def _walk_images(
    root: Path,
    cancel_event: threading.Event | None = None,
    *,
    cancel_every: int = 256,
    walk_progress_every: int = 320,
    walk_progress: Callable[[int, str], None] | None = None,
) -> list[Path]:
    """Collect image paths under the mount's Camera Roll (DCIM) when present; cooperatively cancel."""
    out: list[Path] = []
    n = 0
    root_r = _effective_dcim_scan_root(root)
    for p in root_r.rglob("*"):
        n += 1
        if cancel_event and cancel_every > 0 and n % cancel_every == 0 and cancel_event.is_set():
            raise ScanCancelled([])
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            out.append(p)
            if walk_progress and len(out) % walk_progress_every == 0:
                walk_progress(len(out), _rel_under_mount(root.resolve(), p))
    return _dedupe_heif_jpeg_pairs(out)


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


def fuzzy_roll_cache_path(scan_artifacts_dir: Path, mount_udid: str | None, mount_root: Path) -> Path:
    """Stable JSON path for fuzzy-roll pHash cache (per device + mount root)."""
    safe_udid = "".join(c if c.isalnum() else "_" for c in str(mount_udid or "no_udid"))
    root_tag = hashlib.sha256(str(mount_root.resolve()).encode()).hexdigest()[:12]
    base = scan_artifacts_dir / "fuzzy_roll"
    return (base / f"{safe_udid}_{root_tag}.json").resolve()


def _fuzzy_burst_index_ranges(
    n: int,
    hashes: list[imagehash.ImageHash | None],
    max_adjacent_hamming: int,
    cancel_check: Callable[[], None],
) -> list[tuple[int, int]]:
    """Maximal adjacent chains with length >= 2; each tuple is (start_idx, end_idx) inclusive."""
    ranges: list[tuple[int, int]] = []
    i = 0
    while i < n:
        cancel_check()
        j = i
        while j + 1 < n:
            cancel_check()
            hi, hj = hashes[j], hashes[j + 1]
            if hi is None or hj is None or (hi - hj) > max_adjacent_hamming:
                break
            j += 1
        if j > i:
            ranges.append((i, j))
        i = j + 1
    return ranges


def _ranges_to_raw_groups(files: list[Path], ranges: list[tuple[int, int]]) -> list[list[Path]]:
    out: list[list[Path]] = []
    for a, b in ranges:
        out.append([Path(files[k]) for k in range(a, b + 1)])
    return out


def _fuzzy_ranges_for_batch(
    ranges: list[tuple[int, int]],
    batch_start: int,
    win_end: int,
) -> list[tuple[int, int]]:
    """Keep chains whose first index lies in [batch_start, win_end)."""
    picked: list[tuple[int, int]] = []
    for a, b in ranges:
        if batch_start <= a < win_end:
            picked.append((a, b))
    return picked


def _load_fuzzy_roll_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save_fuzzy_roll_cache(
    path: Path,
    *,
    mount_root: Path,
    mount_udid: str | None,
    phash_max_dim: int,
    max_adjacent_hamming: int,
    paths: list[Path],
    hashes: list[imagehash.ImageHash | None],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serial_hashes: list[str | None] = []
    for h in hashes:
        if h is None:
            serial_hashes.append(None)
        else:
            serial_hashes.append(str(h))
    payload = {
        # Bump when manifest rules change (e.g. DCIM-only walk, HEIF/JPEG dedupe).
        "version": 2,
        "mount_root": str(mount_root.resolve()),
        "mount_udid": mount_udid or "",
        "phash_max_dim": phash_max_dim,
        "max_adjacent_hamming": max_adjacent_hamming,
        "paths": [str(p.resolve()) for p in paths],
        "hashes": serial_hashes,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_matches_mount(
    data: dict[str, Any],
    mount_root: Path,
    mount_udid: str | None,
    phash_max_dim: int,
    max_adjacent_hamming: int,
) -> bool:
    try:
        if int(data.get("version") or 0) != 2:
            return False
        return (
            str(data.get("mount_root") or "") == str(mount_root.resolve())
            and str(data.get("mount_udid") or "") == str(mount_udid or "")
            and int(data.get("phash_max_dim") or -1) == phash_max_dim
            and int(data.get("max_adjacent_hamming") or -1) == max_adjacent_hamming
            and isinstance(data.get("paths"), list)
            and isinstance(data.get("hashes"), list)
            and len(data["paths"]) == len(data["hashes"])
        )
    except Exception:
        return False


def _deserialize_fuzzy_hashes(raw: list[Any]) -> list[imagehash.ImageHash | None]:
    out: list[imagehash.ImageHash | None] = []
    for x in raw:
        if x is None or x == "":
            out.append(None)
            continue
        try:
            out.append(imagehash.hex_to_hash(str(x)))
        except Exception:
            out.append(None)
    return out


def run_fuzzy_roll_scan_batch(
    mount_root: Path,
    *,
    scan_artifacts_dir: Path,
    mount_udid: str | None,
    batch_start: int,
    batch_size: int,
    phash_max_dim: int,
    max_adjacent_hamming: int,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    One interactive fuzzy batch: ensure sorted path manifest + pHash cache, hash only the next slice,
    return burst groups whose chain starts in this slice. Persists partial hashes for resume.

    Returns (groups, next_start_index, total_images).
    """
    root_r = mount_root.resolve()
    cache_path = fuzzy_roll_cache_path(scan_artifacts_dir, mount_udid, root_r)

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled([])

    paths: list[Path]
    hashes: list[imagehash.ImageHash | None]

    loaded = _load_fuzzy_roll_cache(cache_path)
    if loaded and _cache_matches_mount(loaded, root_r, mount_udid, phash_max_dim, max_adjacent_hamming):
        paths = [Path(p) for p in loaded["paths"]]
        hashes = _deserialize_fuzzy_hashes(list(loaded["hashes"]))
        if progress_callback:
            progress_callback(
                0, max(len(paths), 1), f"[fuzzy-roll-batch] Loaded fuzzy pHash cache — {len(paths)} image(s) in roll order."
            )
    else:
        def walk_prog(n_img: int, rel: str) -> None:
            if progress_callback:
                progress_callback(
                    n_img, 0, f"[fuzzy-roll-batch] Walking phone library — {n_img} image(s) so far — {rel}"
                )

        paths = _walk_images(
            mount_root,
            cancel_event,
            walk_progress=walk_prog if progress_callback else None,
        )
        paths.sort(key=lambda p: (_exif_capture_ts(p), str(p)))
        hashes = [None] * len(paths)
        if progress_callback and paths:
            progress_callback(
                0,
                max(len(paths), 1),
                f"[fuzzy-roll-batch] Roll order: {len(paths)} image(s); building fuzzy index (one-time walk + sort)…",
            )
        _save_fuzzy_roll_cache(
            cache_path,
            mount_root=root_r,
            mount_udid=mount_udid,
            phash_max_dim=phash_max_dim,
            max_adjacent_hamming=max_adjacent_hamming,
            paths=paths,
            hashes=hashes,
        )

    try:
        n = len(paths)
        if n == 0:
            return ([], 0, 0)

        batch_start = max(0, min(batch_start, n))
        if batch_start >= n:
            return ([], n, n)

        win_end = min(n, batch_start + max(1, batch_size))
        hash_lo = max(0, batch_start - 1)
        hash_hi = win_end

        pending = sum(1 for i in range(hash_lo, hash_hi) if hashes[i] is None)
        done = 0
        if pending == 0 and progress_callback:
            progress_callback(
                1,
                1,
                f"[fuzzy-roll-batch] Slice [{batch_start}, {win_end}) — all pHashes in slice already cached; analyzing adjacent bursts…",
            )
        for i in range(hash_lo, hash_hi):
            _check_cancel()
            if hashes[i] is not None:
                continue
            p = paths[i]
            hashes[i] = _phash(p, phash_max_dim, cancel_check=_check_cancel)
            done += 1
            if progress_callback and pending > 0:
                rel = _rel_under_mount(root_r, p)
                progress_callback(
                    done,
                    max(pending, 1),
                    f"[fuzzy-roll-batch] Slice [{batch_start}, {win_end}) pHash {done}/{pending} in this slice — {rel}",
                )

        _save_fuzzy_roll_cache(
            cache_path,
            mount_root=root_r,
            mount_udid=mount_udid,
            phash_max_dim=phash_max_dim,
            max_adjacent_hamming=max_adjacent_hamming,
            paths=paths,
            hashes=hashes,
        )

        all_ranges = _fuzzy_burst_index_ranges(n, hashes, max_adjacent_hamming, _check_cancel)
        slice_ranges = _fuzzy_ranges_for_batch(all_ranges, batch_start, win_end)
        raw = _ranges_to_raw_groups(paths, slice_ranges)
        groups = finalize_groups(raw, scan_kind="fuzzy")
        next_start = win_end
        return (groups, next_start, n)
    except ScanCancelled:
        if paths and hashes and len(paths) == len(hashes):
            try:
                _save_fuzzy_roll_cache(
                    cache_path,
                    mount_root=root_r,
                    mount_udid=mount_udid,
                    phash_max_dim=phash_max_dim,
                    max_adjacent_hamming=max_adjacent_hamming,
                    paths=paths,
                    hashes=hashes,
                )
            except OSError:
                pass
        raise


def scan_duplicates(
    mount_root: Path,
    phash_threshold: int,
    phash_max_dim: int = 256,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    root_r = mount_root.resolve()

    def walk_prog(n_img: int, rel: str) -> None:
        if progress_callback:
            progress_callback(n_img, 0, f"[exact-dup] Walking phone library — {n_img} image(s) so far — {rel}")

    files = _walk_images(
        mount_root,
        cancel_event,
        walk_progress=walk_prog if progress_callback else None,
    )
    if progress_callback and files:
        progress_callback(
            len(files),
            max(len(files), 1),
            f"[exact-dup] Finished listing {len(files)} image(s); grouping by file size…",
        )
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
                rel0 = _rel_under_mount(root_r, bucket[0]) if bucket else ""
                progress_callback(
                    processed,
                    len(files),
                    f"[exact-dup] Size bucket {size} B: only one file — {rel0}",
                )
            continue
        hashes: list[imagehash.ImageHash | None] = []
        for p in bucket:
            _check_cancel()
            hashes.append(_phash(p, phash_max_dim, cancel_check=_check_cancel))
            processed += 1
            if progress_callback:
                rel = _rel_under_mount(root_r, p)
                progress_callback(processed, len(files), f"[exact-dup] Perceptual hash {processed}/{len(files)} — {rel}")
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

    Hashes the entire library in one pass (used by tests and callers that want a full scan).
    The web UI uses :func:`run_fuzzy_roll_scan_batch` for interactive slices + disk cache.
    """
    root_r = mount_root.resolve()

    def walk_prog(n_img: int, rel: str) -> None:
        if progress_callback:
            progress_callback(n_img, 0, f"[fuzzy-roll-full] Walking phone library — {n_img} image(s) so far — {rel}")

    files = _walk_images(
        mount_root,
        cancel_event,
        walk_progress=walk_prog if progress_callback else None,
    )
    files.sort(key=lambda p: (_exif_capture_ts(p), str(p)))
    n = len(files)
    if progress_callback and files:
        progress_callback(0, max(n, 1), f"[fuzzy-roll-full] Roll order: {n} image(s); hashing for fuzzy bursts…")

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled([])

    hashes: list[imagehash.ImageHash | None] = []
    for i, p in enumerate(files):
        _check_cancel()
        hashes.append(_phash(p, phash_max_dim, cancel_check=_check_cancel))
        if progress_callback:
            rel = _rel_under_mount(root_r, p)
            progress_callback(i + 1, max(n, 1), f"[fuzzy-roll-full] Fuzzy pre-hash {i + 1}/{n} — {rel}")

    ranges = _fuzzy_burst_index_ranges(n, hashes, max_adjacent_hamming, _check_cancel)
    completed_bursts = _ranges_to_raw_groups(files, ranges)
    return finalize_groups(completed_bursts, scan_kind="fuzzy")


def write_artifact(path: Path, groups: list[dict[str, Any]], *, scan_kind: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sk = scan_kind or (groups[0].get("scan_kind") if groups else None) or "exact"
    payload = {"scan_kind": sk, "groups": groups}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_artifact(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("groups") or [])
