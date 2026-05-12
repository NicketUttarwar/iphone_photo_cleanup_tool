"""Safe deletes under mount root only."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_under_mount(candidate: Path, mount_root: Path) -> Path | None:
    try:
        mroot = mount_root.resolve()
        cand = candidate.resolve()
    except OSError:
        return None
    try:
        cand.relative_to(mroot)
    except ValueError:
        return None
    if any(part == ".." for part in candidate.parts):
        return None
    if not cand.is_file():
        return None
    return cand


def delete_paths(paths: list[str], mount_root: Path) -> dict[str, Any]:
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    skipped: list[str] = []
    for raw in paths:
        p = Path(raw)
        safe = resolve_under_mount(p, mount_root)
        if safe is None:
            skipped.append(raw)
            continue
        try:
            safe.unlink()
            deleted.append(str(safe))
        except OSError as e:
            failed.append({"path": raw, "error": str(e)})
    return {"deleted": deleted, "failed": failed, "skipped": skipped}


def delete_paths_chunked(
    paths: list[str],
    mount_root: Path,
    chunk_size: int,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """Delete in chunks; optional callback(done, total, deleted_n, failed_n, skipped_n)."""

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    skipped: list[str] = []
    n = len(paths)
    step = max(1, int(chunk_size))
    for i in range(0, n, step):
        chunk = paths[i : i + step]
        part = delete_paths(chunk, mount_root)
        deleted.extend(part["deleted"])
        failed.extend(part["failed"])
        skipped.extend(part["skipped"])
        if on_progress:
            done = min(i + len(chunk), n)
            on_progress(done, n, len(deleted), len(failed), len(skipped))
    return {"deleted": deleted, "failed": failed, "skipped": skipped}
