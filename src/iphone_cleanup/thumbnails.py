"""On-demand JPEG thumbnails with disk cache (bounded by config)."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass


def _cache_key(mount_root: Path, source: Path, max_edge: int, quality: int) -> str:
    try:
        st = source.stat()
        meta = f"{source.resolve()}|{st.st_mtime_ns}|{st.st_size}|{max_edge}|{quality}"
    except OSError:
        meta = f"{source}|{max_edge}|{quality}"
    return hashlib.sha256(meta.encode("utf-8")).hexdigest()


def clear_jpeg_cache(cache_dir: Path) -> int:
    """Remove generated JPEG entries (and stray .tmp) from the thumbnail cache directory."""

    if not cache_dir.is_dir():
        return 0
    n = 0
    for pattern in ("*.jpg", "*.jpeg", "*.tmp"):
        for p in cache_dir.glob(pattern):
            try:
                p.unlink(missing_ok=True)
                n += 1
            except OSError:
                continue
    return n


def _enforce_cache_budget(cache_dir: Path, max_mb: int) -> None:
    if max_mb <= 0:
        return
    files: list[tuple[float, Path]] = []
    total = 0
    for p in cache_dir.glob("*.jpg"):
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, p))
        total += st.st_size
    budget = max_mb * 1024 * 1024
    if total <= budget:
        return
    files.sort(key=lambda t: t[0])
    for _, p in files:
        try:
            st = p.stat()
            p.unlink(missing_ok=True)
            total -= st.st_size
        except OSError:
            continue
        if total <= budget:
            break


def get_thumbnail_jpeg(
    source: Path,
    mount_root: Path,
    cache_dir: Path,
    max_edge: int,
    quality: int,
    cache_max_mb: int,
) -> bytes:
    try:
        source.resolve().relative_to(mount_root.resolve())
    except ValueError:
        raise PermissionError("Path outside mount root")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(mount_root, source, max_edge, quality)
    cache_path = cache_dir / f"{key}.jpg"
    if cache_path.is_file():
        return cache_path.read_bytes()
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
    tmp = cache_dir / f"{key}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, cache_path)
    _enforce_cache_budget(cache_dir, cache_max_mb)
    return data
