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
from iphone_cleanup.fuzzy_palette import (
    FuzzyFeatures,
    FuzzyMatchConfig,
    adjacent_fuzzy_link,
    extract_fuzzy_features_from_path,
    vote_chain_metadata,
)

FUZZY_CACHE_VERSION = 3

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
    walk_progress_every: int = 250,
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


def finalize_groups(
    raw_groups: list[list[Path]],
    *,
    scan_kind: str,
    group_extras: list[dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    out_groups: list[dict[str, Any]] = []
    for gi, paths in enumerate(raw_groups):
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
        row: dict[str, Any] = {
            "id": gid,
            "paths": [str(Path(x).resolve()) for x in paths],
            "bytesSavedIfOneKept": max(0, total - largest_sz),
            "recommendedKeep": largest_s,
            "recommendedKeeps": [largest_s],
            "scan_kind": scan_kind,
        }
        if group_extras and gi < len(group_extras) and group_extras[gi]:
            row.update(group_extras[gi])
        out_groups.append(row)
    return out_groups


def finalize_duplicate_groups(raw_groups: list[list[Path]]) -> list[dict[str, Any]]:
    return finalize_groups(raw_groups, scan_kind="exact")


def fuzzy_roll_cache_path(scan_artifacts_dir: Path, mount_udid: str | None, mount_root: Path) -> Path:
    """Stable JSON path for fuzzy-roll pHash cache (per device + mount root)."""
    safe_udid = "".join(c if c.isalnum() else "_" for c in str(mount_udid or "no_udid"))
    root_tag = hashlib.sha256(str(mount_root.resolve()).encode()).hexdigest()[:12]
    base = scan_artifacts_dir / "fuzzy_roll"
    return (base / f"{safe_udid}_{root_tag}.json").resolve()


def _default_fuzzy_match_config(phash_max_dim: int, max_adjacent_hamming: int) -> FuzzyMatchConfig:
    return FuzzyMatchConfig(
        phash_max_dim=phash_max_dim,
        max_adjacent_hamming=max_adjacent_hamming,
    )


def _adjacent_fuzzy_linked(
    features: list[FuzzyFeatures | None],
    i: int,
    j: int,
    cfg: FuzzyMatchConfig,
) -> tuple[bool, str, str]:
    fa, fb = features[i], features[j]
    if fa is None or fb is None:
        return False, "", ""
    return adjacent_fuzzy_link(fa, fb, cfg)


def _fuzzy_burst_starts_in_window(
    n: int,
    features: list[FuzzyFeatures | None],
    cfg: FuzzyMatchConfig,
    cancel_check: Callable[[], None],
    window_start: int,
    window_end: int,
) -> list[tuple[int, int, dict[str, Any]]]:
    """Burst chains whose first index lies in [window_start, window_end)."""
    ranges: list[tuple[int, int, dict[str, Any]]] = []
    lo = max(0, min(window_start, n))
    hi = max(lo, min(window_end, n))
    for i in range(lo, hi):
        cancel_check()
        if i > 0 and _adjacent_fuzzy_linked(features, i - 1, i, cfg)[0]:
            continue
        j = i
        link_reasons: list[str] = []
        link_strengths: list[str] = []
        while j + 1 < n:
            cancel_check()
            ok, reason, strength = _adjacent_fuzzy_linked(features, j, j + 1, cfg)
            if not ok:
                break
            if reason:
                link_reasons.append(reason)
                link_strengths.append(strength)
            j += 1
        if j > i:
            reason, strength = vote_chain_metadata(link_reasons, link_strengths)
            ranges.append((i, j, {"fuzzy_match_reason": reason, "fuzzy_link_strength": strength}))
    return ranges


def _roll_sort_key(path: Path, *, use_exif: bool) -> tuple[Any, ...]:
    """Fast mtime+path ordering for large libraries; optional full EXIF pass."""
    if use_exif:
        return (_exif_capture_ts(path), str(path))
    try:
        return (path.stat().st_mtime, str(path))
    except OSError:
        return (0.0, str(path))


def _fuzzy_burst_index_ranges(
    n: int,
    features: list[FuzzyFeatures | None],
    cfg: FuzzyMatchConfig,
    cancel_check: Callable[[], None],
) -> list[tuple[int, int, dict[str, Any]]]:
    """Maximal adjacent chains with length >= 2."""
    ranges: list[tuple[int, int, dict[str, Any]]] = []
    i = 0
    while i < n:
        cancel_check()
        j = i
        link_reasons: list[str] = []
        link_strengths: list[str] = []
        while j + 1 < n:
            cancel_check()
            ok, reason, strength = _adjacent_fuzzy_linked(features, j, j + 1, cfg)
            if not ok:
                break
            if reason:
                link_reasons.append(reason)
                link_strengths.append(strength)
            j += 1
        if j > i:
            reason, strength = vote_chain_metadata(link_reasons, link_strengths)
            ranges.append((i, j, {"fuzzy_match_reason": reason, "fuzzy_link_strength": strength}))
        i = j + 1
    return ranges


def _ranges_to_raw_groups(files: list[Path], ranges: list[tuple[int, int]]) -> list[list[Path]]:
    out: list[list[Path]] = []
    for a, b in ranges:
        out.append([Path(files[k]) for k in range(a, b + 1)])
    return out


def _ranges_to_raw_groups_with_meta(
    files: list[Path],
    ranges: list[tuple[int, int, dict[str, Any]]],
) -> tuple[list[list[Path]], list[dict[str, Any]]]:
    raw: list[list[Path]] = []
    meta: list[dict[str, Any]] = []
    for a, b, m in ranges:
        raw.append([Path(files[k]) for k in range(a, b + 1)])
        meta.append(dict(m))
    return raw, meta


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


def _fuzzy_cfg_cache_fields(cfg: FuzzyMatchConfig) -> dict[str, Any]:
    return {
        "phash_max_dim": cfg.phash_max_dim,
        "max_adjacent_hamming": cfg.max_adjacent_hamming,
        "colorhash_max_hamming": cfg.colorhash_max_hamming,
        "max_adjacent_gap_sec": cfg.max_adjacent_gap_sec,
        "palette_enabled": cfg.palette_enabled,
        "palette_max_distance": cfg.palette_max_distance,
        "palette_max_color_count_delta": cfg.palette_max_color_count_delta,
        "palette_min_grid_agreement": cfg.palette_min_grid_agreement,
        "palette_grid": cfg.palette_grid,
        "fast_path_enabled": cfg.fast_path_enabled,
        "grid_exact_match_min": cfg.grid_exact_match_min,
    }


def _serialize_features_list(features: list[FuzzyFeatures | None]) -> tuple[
    list[str | None],
    list[str | None],
    list[str | None],
    list[float | None],
]:
    hashes: list[str | None] = []
    colorhashes: list[str | None] = []
    palettes: list[str | None] = []
    timestamps: list[float | None] = []
    for f in features:
        if f is None:
            hashes.append(None)
            colorhashes.append(None)
            palettes.append(None)
            timestamps.append(None)
            continue
        hashes.append(str(f.phash) if f.phash is not None else None)
        colorhashes.append(str(f.colorhash) if f.colorhash is not None else None)
        palettes.append(f.palette.serialize() if f.palette is not None else None)
        timestamps.append(f.capture_ts)
    return hashes, colorhashes, palettes, timestamps


def _save_fuzzy_roll_cache(
    path: Path,
    *,
    mount_root: Path,
    mount_udid: str | None,
    cfg: FuzzyMatchConfig,
    paths: list[Path],
    features: list[FuzzyFeatures | None],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    hashes, colorhashes, palettes, timestamps = _serialize_features_list(features)
    payload: dict[str, Any] = {
        "version": FUZZY_CACHE_VERSION,
        "mount_root": str(mount_root.resolve()),
        "mount_udid": mount_udid or "",
        "paths": [str(p.resolve()) for p in paths],
        "hashes": hashes,
        "colorhashes": colorhashes,
        "palette_sigs": palettes,
        "capture_ts": timestamps,
    }
    payload.update(_fuzzy_cfg_cache_fields(cfg))
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _cache_matches_mount(
    data: dict[str, Any],
    mount_root: Path,
    mount_udid: str | None,
    cfg: FuzzyMatchConfig,
) -> bool:
    try:
        ver = int(data.get("version") or 0)
        if ver not in (2, FUZZY_CACHE_VERSION):
            return False
        if str(data.get("mount_root") or "") != str(mount_root.resolve()):
            return False
        if str(data.get("mount_udid") or "") != str(mount_udid or ""):
            return False
        if not isinstance(data.get("paths"), list) or not isinstance(data.get("hashes"), list):
            return False
        if len(data["paths"]) != len(data["hashes"]):
            return False
        if ver == FUZZY_CACHE_VERSION:
            for key, val in _fuzzy_cfg_cache_fields(cfg).items():
                if data.get(key) != val:
                    return False
        else:
            if int(data.get("phash_max_dim") or -1) != cfg.phash_max_dim:
                return False
            if int(data.get("max_adjacent_hamming") or -1) != cfg.max_adjacent_hamming:
                return False
        return True
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


def _deserialize_features_from_cache(
    data: dict[str, Any],
    cfg: FuzzyMatchConfig,
) -> list[FuzzyFeatures | None]:
    n = len(data.get("paths") or [])
    hashes = _deserialize_fuzzy_hashes(list(data.get("hashes") or []))
    raw_ch = list(data.get("colorhashes") or [None] * n)
    raw_pl = list(data.get("palette_sigs") or [None] * n)
    raw_ts = list(data.get("capture_ts") or [None] * n)
    grid_side = cfg.grid_side
    out: list[FuzzyFeatures | None] = []
    for i in range(n):
        ph = hashes[i] if i < len(hashes) else None
        ch: imagehash.ImageHash | None = None
        if i < len(raw_ch) and raw_ch[i]:
            try:
                ch = imagehash.hex_to_hash(str(raw_ch[i]))
            except Exception:
                ch = None
        pal = None
        if i < len(raw_pl) and raw_pl[i]:
            from iphone_cleanup.fuzzy_palette import PaletteSignature

            pal = PaletteSignature.deserialize(str(raw_pl[i]), grid_side=grid_side)
        ts = None
        if i < len(raw_ts) and raw_ts[i] is not None:
            try:
                ts = float(raw_ts[i])
            except (TypeError, ValueError):
                ts = None
        if ph is None and ch is None and pal is None:
            out.append(None)
        else:
            out.append(FuzzyFeatures(phash=ph, colorhash=ch, palette=pal, capture_ts=ts))
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
    fuzzy_match_config: FuzzyMatchConfig | None = None,
    fuzzy_roll_sort_exif: bool = False,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    One interactive fuzzy batch: sorted manifest + feature cache, analyze adjacent multi-signal chains.

    Returns (groups, next_start_index, total_images).
    """
    cfg = fuzzy_match_config or _default_fuzzy_match_config(phash_max_dim, max_adjacent_hamming)
    root_r = mount_root.resolve()
    cache_path = fuzzy_roll_cache_path(scan_artifacts_dir, mount_udid, root_r)

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled([])

    paths: list[Path]
    features: list[FuzzyFeatures | None]

    loaded = _load_fuzzy_roll_cache(cache_path)
    if loaded and _cache_matches_mount(loaded, root_r, mount_udid, cfg):
        paths = [Path(p) for p in loaded["paths"]]
        features = _deserialize_features_from_cache(loaded, cfg)
        if progress_callback:
            progress_callback(
                0,
                max(len(paths), 1),
                f"[fuzzy-roll-batch] Loaded fuzzy feature cache — {len(paths)} image(s) in roll order.",
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
            walk_progress_every=250,
            walk_progress=walk_prog if progress_callback else None,
        )
        sort_label = "EXIF capture time" if fuzzy_roll_sort_exif else "file date + path (fast)"
        if progress_callback and paths:
            progress_callback(
                0,
                max(len(paths), 1),
                f"[fuzzy-roll-batch] Sorting {len(paths)} image(s) by {sort_label} (one-time for this mount)…",
            )
        paths.sort(key=lambda p: _roll_sort_key(p, use_exif=fuzzy_roll_sort_exif))
        features = [None] * len(paths)
        if progress_callback and paths:
            progress_callback(
                0,
                max(len(paths), 1),
                f"[fuzzy-roll-batch] Indexed {len(paths)} image(s); features filled batch-by-batch.",
            )
        _save_fuzzy_roll_cache(
            cache_path,
            mount_root=root_r,
            mount_udid=mount_udid,
            cfg=cfg,
            paths=paths,
            features=features,
        )

    try:
        n = len(paths)
        if n == 0:
            return ([], 0, 0)

        batch_start = max(0, min(batch_start, n))
        if batch_start >= n:
            return ([], n, n)

        if batch_size <= 0:
            win_end = n
        else:
            win_end = min(n, batch_start + max(1, batch_size))
        hash_lo = max(0, batch_start - 1)
        hash_hi = win_end

        pending = sum(1 for i in range(hash_lo, hash_hi) if features[i] is None)
        done = 0
        if pending == 0 and progress_callback:
            progress_callback(
                1,
                1,
                f"[fuzzy-roll-batch] Slice [{batch_start}, {win_end}) — features cached; analyzing adjacent sets…",
            )
        for i in range(hash_lo, hash_hi):
            _check_cancel()
            if features[i] is not None:
                continue
            p = paths[i]
            ts = float(_exif_capture_ts(p))
            features[i] = extract_fuzzy_features_from_path(
                p,
                max_dim=cfg.phash_max_dim,
                grid_side=cfg.grid_side,
                capture_ts=ts,
                cancel_check=_check_cancel,
            )
            done += 1
            if progress_callback and pending > 0:
                rel = _rel_under_mount(root_r, p)
                progress_callback(
                    done,
                    max(pending, 1),
                    f"[fuzzy-roll-batch] Slice [{batch_start}, {win_end}) features {done}/{pending} — {rel}",
                )

        _save_fuzzy_roll_cache(
            cache_path,
            mount_root=root_r,
            mount_udid=mount_udid,
            cfg=cfg,
            paths=paths,
            features=features,
        )

        slice_ranges = _fuzzy_burst_starts_in_window(
            n, features, cfg, _check_cancel, batch_start, win_end
        )
        raw, meta = _ranges_to_raw_groups_with_meta(paths, slice_ranges)
        groups = finalize_groups(raw, scan_kind="fuzzy", group_extras=meta)
        next_start = win_end
        return (groups, next_start, n)
    except ScanCancelled:
        if paths and features and len(paths) == len(features):
            try:
                _save_fuzzy_roll_cache(
                    cache_path,
                    mount_root=root_r,
                    mount_udid=mount_udid,
                    cfg=cfg,
                    paths=paths,
                    features=features,
                )
            except OSError:
                pass
        raise


def scan_duplicates(
    mount_root: Path,
    phash_threshold: int,
    phash_max_dim: int = 256,
    *,
    exact_max_hash_cluster: int = 150,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
    walk_progress_every: int = 250,
) -> tuple[list[dict[str, Any]], int]:
    root_r = mount_root.resolve()

    def walk_prog(n_img: int, rel: str) -> None:
        if progress_callback:
            progress_callback(n_img, 0, f"[exact-dup] Walking phone library — {n_img} image(s) so far — {rel}")

    files = _walk_images(
        mount_root,
        cancel_event,
        walk_progress_every=walk_progress_every,
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

        def _add_pair(i: int, j: int) -> None:
            nonlocal pair_ops
            pair_ops += 1
            if pair_ops % 128 == 0:
                _check_cancel()
            hi, hj = hashes[i], hashes[j]
            if hi is None or hj is None:
                return
            if hi - hj <= phash_threshold:
                pairs.append((i, j))

        compare_sets: list[list[int]] = [idxs] if len(idxs) <= 120 else []
        if not compare_sets:
            by_prefix: dict[str, list[int]] = defaultdict(list)
            for i in idxs:
                h = hashes[i]
                if h is not None:
                    by_prefix[str(h)[:8]].append(i)
            compare_sets = list(by_prefix.values())
        for sub in compare_sets:
            if exact_max_hash_cluster > 0 and len(sub) > exact_max_hash_cluster:
                if progress_callback:
                    progress_callback(
                        processed,
                        len(files),
                        f"[exact-dup] Comparing {len(sub)}-image hash cluster in chunks "
                        f"(>{exact_max_hash_cluster} files; full library, no skip)…",
                    )
                chunk = exact_max_hash_cluster
                for start in range(0, len(sub), chunk):
                    part = sub[start : start + chunk]
                    for ii in range(len(part)):
                        for jj in range(ii + 1, len(part)):
                            _add_pair(part[ii], part[jj])
                    if start + chunk < len(sub):
                        bridge = sub[start + chunk - 1 : start + chunk + 1]
                        for ii in range(len(bridge)):
                            for jj in range(ii + 1, len(bridge)):
                                _add_pair(bridge[ii], bridge[jj])
                continue
            for ii in range(len(sub)):
                for jj in range(ii + 1, len(sub)):
                    _add_pair(sub[ii], sub[jj])
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

    return finalize_duplicate_groups(raw_groups), len(files)


def scan_fuzzy_roll_bursts(
    mount_root: Path,
    *,
    phash_max_dim: int,
    max_adjacent_hamming: int,
    fuzzy_match_config: FuzzyMatchConfig | None = None,
    progress_callback: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """
    Group consecutive roll-order photos via multi-signal adjacent linking (pHash, colorhash, palette).

    The web UI uses :func:`run_fuzzy_roll_scan_batch` for interactive slices + disk cache.
    """
    cfg = fuzzy_match_config or _default_fuzzy_match_config(phash_max_dim, max_adjacent_hamming)
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
        progress_callback(0, max(n, 1), f"[fuzzy-roll-full] Roll order: {n} image(s); extracting fuzzy features…")

    def _check_cancel() -> None:
        if cancel_event and cancel_event.is_set():
            raise ScanCancelled([])

    feat_list: list[FuzzyFeatures | None] = []
    for i, p in enumerate(files):
        _check_cancel()
        ts = _exif_capture_ts(p)
        feat_list.append(
            extract_fuzzy_features_from_path(
                p,
                max_dim=cfg.phash_max_dim,
                grid_side=cfg.grid_side,
                capture_ts=ts if ts else None,
                cancel_check=_check_cancel,
            )
        )
        if progress_callback:
            rel = _rel_under_mount(root_r, p)
            progress_callback(i + 1, max(n, 1), f"[fuzzy-roll-full] Fuzzy features {i + 1}/{n} — {rel}")

    ranges = _fuzzy_burst_index_ranges(n, feat_list, cfg, _check_cancel)
    raw, meta = _ranges_to_raw_groups_with_meta(files, ranges)
    return finalize_groups(raw, scan_kind="fuzzy", group_extras=meta)


def fuzzy_palette_scipy_status() -> str:
    from iphone_cleanup.fuzzy_palette import scipy_available

    return "yes" if scipy_available() else "fallback_intersection"


def fuzzy_match_config_from_settings(settings: Any) -> FuzzyMatchConfig:
    """Build fuzzy link config from app Settings."""
    return FuzzyMatchConfig(
        phash_max_dim=int(settings.fuzzy_phash_max_dim),
        max_adjacent_hamming=int(settings.fuzzy_phash_max_hamming),
        colorhash_max_hamming=int(settings.fuzzy_colorhash_max_hamming),
        max_adjacent_gap_sec=float(settings.fuzzy_max_adjacent_gap_sec),
        palette_enabled=bool(settings.fuzzy_palette_enabled),
        palette_max_distance=float(settings.fuzzy_palette_max_distance),
        palette_max_color_count_delta=int(settings.fuzzy_palette_max_color_count_delta),
        palette_min_grid_agreement=float(settings.fuzzy_palette_min_grid_agreement),
        palette_grid=int(settings.fuzzy_palette_grid),
        fast_path_enabled=bool(settings.fuzzy_fast_path_enabled),
        grid_exact_match_min=int(settings.fuzzy_grid_exact_match_min),
    )


def write_artifact(
    path: Path,
    groups: list[dict[str, Any]],
    *,
    scan_kind: str | None = None,
    group_keep: dict[str, list[str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sk = scan_kind or (groups[0].get("scan_kind") if groups else None) or "exact"
    payload: dict[str, Any] = {"scan_kind": sk, "groups": groups}
    if group_keep is not None:
        payload["group_keep"] = group_keep
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_artifact(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("groups") or [])
