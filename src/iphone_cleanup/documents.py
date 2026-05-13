"""Find document-like photos on the mounted library and quarantine-before-delete flows.

Assumes Apple-side tagging when present (Finder / Photos user tags on the file via
``com.apple.metadata:_kMDItemUserTags``). Optional path keywords and a lightweight
luminance heuristic can widen matching for local-only workflows.
"""

from __future__ import annotations

import json
import plistlib
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from iphone_cleanup import delete as delmod
from iphone_cleanup.scan import IMAGE_EXTS

Scope = Literal["all", "older_than_90d"]

USER_TAGS_XATTR = "com.apple.metadata:_kMDItemUserTags"

# Relative path or filename hints (Photos exports / some workflows include these).
_PATH_DOC_MARKERS = ("document", "scan", "scanned", "receipt", "paperwork")


def _has_getxattr() -> bool:
    import os

    return hasattr(os, "getxattr")


def _read_apple_user_tags(path: Path) -> list[str]:
    if not _has_getxattr():
        return []
    import os

    try:
        raw = os.getxattr(path, USER_TAGS_XATTR)
    except OSError:
        return []
    if not raw:
        return []
    try:
        val = plistlib.loads(raw)
    except Exception:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None]
    if isinstance(val, str):
        return [val]
    return []


def _tags_include_document(tags: list[str]) -> bool:
    for t in tags:
        tl = str(t).lower()
        if "document" in tl:
            return True
    return False


def _path_suggests_document(rel: str) -> bool:
    lower = rel.lower().replace("\\", "/")
    return any(m in lower for m in _PATH_DOC_MARKERS)


def _gray_mean_std(path: Path) -> tuple[float, float] | None:
    try:
        with Image.open(path) as im:
            im = im.convert("L")
            im.thumbnail((160, 160))
            pixels = list(im.getdata())
    except Exception:
        return None
    if not pixels:
        return None
    n = len(pixels)
    mean = sum(pixels) / n
    var = sum((p - mean) ** 2 for p in pixels) / n
    return mean, var**0.5


def _visual_white_paper_heuristic(path: Path) -> bool:
    """High mean (white page) with moderate std (text/edges). Intentionally conservative."""
    stats = _gray_mean_std(path)
    if stats is None:
        return False
    mean, std = stats
    return mean > 198.0 and 10.0 < std < 65.0


def document_quarantine_root(data_dir: Path) -> Path:
    return (data_dir / "document_quarantine").resolve()


def is_document_candidate(
    path: Path,
    mount_root: Path,
    *,
    include_visual_fallback: bool,
) -> bool:
    try:
        rel = str(path.resolve().relative_to(mount_root.resolve()))
    except ValueError:
        return False
    if path.suffix.lower() not in IMAGE_EXTS:
        return False
    if _tags_include_document(_read_apple_user_tags(path)):
        return True
    if _path_suggests_document(rel) or _path_suggests_document(path.name):
        return True
    if include_visual_fallback and _visual_white_paper_heuristic(path):
        return True
    return False


def iter_document_paths(
    mount_root: Path,
    scope: Scope,
    *,
    include_visual_fallback: bool,
    now: float | None = None,
) -> list[Path]:
    root = mount_root.resolve()
    t0 = time.time() if now is None else float(now)
    cutoff = t0 - 90.0 * 86400.0
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if not is_document_candidate(p, root, include_visual_fallback=include_visual_fallback):
            continue
        if scope == "older_than_90d":
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
        out.append(p)
    return sorted(out, key=lambda x: str(x))


def _manifest_path(batch_dir: Path) -> Path:
    return batch_dir / "manifest.json"


def _write_manifest(batch_dir: Path, entries: list[dict[str, str]]) -> None:
    batch_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(batch_dir).write_text(
        json.dumps({"entries": entries, "created_at": time.time()}, indent=2),
        encoding="utf-8",
    )


def _read_manifest(batch_dir: Path) -> list[dict[str, str]]:
    mp = _manifest_path(batch_dir)
    if not mp.is_file():
        return []
    data = json.loads(mp.read_text(encoding="utf-8"))
    return list(data.get("entries") or [])


def quarantine_and_remove(
    paths: list[Path],
    mount_root: Path,
    data_dir: Path,
    *,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Copy each file into local quarantine, then unlink from mount. Returns manifest summary."""
    root = mount_root.resolve()
    qroot = document_quarantine_root(data_dir)
    bid = batch_id or uuid.uuid4().hex[:12]
    batch_dir = qroot / bid
    batch_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    copied: list[str] = []
    removed: list[str] = []
    failed: list[dict[str, str]] = []

    for i, src in enumerate(paths):
        safe = delmod.resolve_under_mount(src, root)
        if safe is None:
            failed.append({"path": str(src), "error": "outside_mount_or_missing"})
            continue
        suffix = safe.suffix or ".bin"
        stored_name = f"{i:05d}_{uuid.uuid4().hex[:10]}{suffix}"
        dst = batch_dir / stored_name
        try:
            shutil.copy2(safe, dst)
        except OSError as e:
            failed.append({"path": str(safe), "error": f"copy:{e}"})
            continue
        copied.append(str(safe))
        try:
            safe.unlink()
            removed.append(str(safe))
        except OSError as e:
            failed.append({"path": str(safe), "error": f"unlink_after_copy:{e}"})
            # Keep quarantine copy so operator can reconcile manually.
        entries.append({"original": str(safe.resolve()), "stored": stored_name})

    _write_manifest(batch_dir, entries)
    return {
        "batch_id": bid,
        "quarantine_dir": str(batch_dir),
        "copied_then_removed": len(removed),
        "failed": failed,
        "entries": len(entries),
    }


def undo_batch(batch_id: str, mount_root: Path, data_dir: Path) -> dict[str, Any]:
    """Restore quarantined files back onto the mount (same absolute paths)."""
    root = mount_root.resolve()
    batch_dir = document_quarantine_root(data_dir) / batch_id
    entries = _read_manifest(batch_dir)
    restored: list[str] = []
    errors: list[dict[str, str]] = []
    for ent in entries:
        orig_s = ent.get("original") or ""
        stored = ent.get("stored") or ""
        if not orig_s or not stored:
            continue
        orig = Path(orig_s)
        dst = delmod.resolve_under_mount(orig, root)
        src_file = batch_dir / stored
        if not src_file.is_file():
            errors.append({"path": orig_s, "error": "quarantine_missing"})
            continue
        try:
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, orig)
            restored.append(str(orig.resolve()))
        except OSError as e:
            errors.append({"path": orig_s, "error": str(e)})
    # Remove batch dir if nothing failed critically — if partial errors, keep dir for operator.
    if not errors:
        shutil.rmtree(batch_dir, ignore_errors=True)
    return {"restored": restored, "errors": errors, "batch_id": batch_id}


def finalize_batch(batch_id: str, data_dir: Path) -> dict[str, Any]:
    """Drop local quarantine copies (cannot restore to phone after this)."""
    batch_dir = document_quarantine_root(data_dir) / batch_id
    if not batch_dir.is_dir():
        return {"removed": False, "batch_id": batch_id}
    shutil.rmtree(batch_dir, ignore_errors=True)
    return {"removed": True, "batch_id": batch_id}


def finalize_all_batches(data_dir: Path) -> int:
    qroot = document_quarantine_root(data_dir)
    if not qroot.is_dir():
        return 0
    n = 0
    for child in list(qroot.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            n += 1
    return n


def list_batches(data_dir: Path) -> list[dict[str, Any]]:
    qroot = document_quarantine_root(data_dir)
    if not qroot.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(qroot.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not child.is_dir():
            continue
        ents = _read_manifest(child)
        out.append(
            {
                "batch_id": child.name,
                "pending_restore_files": len(ents),
                "path": str(child),
            }
        )
    return out


def _quarantine_bytes_for_entries(batch_dir: Path, entries: list[dict[str, str]]) -> int:
    total = 0
    for ent in entries:
        stored = ent.get("stored") or ""
        if not stored or ".." in stored or "/" in stored or "\\" in stored:
            continue
        p = batch_dir / stored
        if not p.is_file():
            continue
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return total


def finalize_preview_data(
    data_dir: Path,
    *,
    batch_id: str | None = None,
    all_batches: bool = False,
    max_samples: int = 24,
) -> dict[str, Any]:
    """Summarize Mac-side quarantine files that would be dropped by finalize (counts, bytes, thumbnail keys)."""

    qroot = document_quarantine_root(data_dir)
    if not qroot.is_dir():
        return {
            "file_count": 0,
            "total_bytes": 0,
            "batch_count": 0,
            "samples": [],
        }

    dirs: list[Path] = []
    if batch_id:
        d = (qroot / batch_id).resolve()
        try:
            d.relative_to(qroot.resolve())
        except ValueError:
            return {"file_count": 0, "total_bytes": 0, "batch_count": 0, "samples": [], "error": "invalid_batch"}
        if d.is_dir():
            dirs = [d]
    elif all_batches:
        dirs = [p.resolve() for p in qroot.iterdir() if p.is_dir()]
    else:
        dirs = []

    file_count = 0
    total_bytes = 0
    samples: list[dict[str, str]] = []
    for batch_dir in dirs:
        try:
            batch_dir.relative_to(qroot.resolve())
        except ValueError:
            continue
        ents = _read_manifest(batch_dir)
        file_count += len(ents)
        total_bytes += _quarantine_bytes_for_entries(batch_dir, ents)
        bid = batch_dir.name
        for ent in ents:
            if len(samples) >= max_samples:
                break
            stored = ent.get("stored") or ""
            if not stored or ".." in stored or "/" in stored or "\\" in stored:
                continue
            if (batch_dir / stored).is_file():
                samples.append({"batch_id": bid, "stored": stored})

    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "batch_count": len(dirs),
        "samples": samples,
    }
