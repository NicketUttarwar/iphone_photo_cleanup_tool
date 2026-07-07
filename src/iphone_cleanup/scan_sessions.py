"""Persistent scan sessions under repo-root ``user_scans/`` (runtime data, gitignored)."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from iphone_cleanup import auto_best, scan
from iphone_cleanup.state import Phase

if TYPE_CHECKING:
    from iphone_cleanup.app_context import AppCtx

SESSIONS_DIR = "sessions"
ACTIVE_FILE = "active.json"
MANIFEST_NAME = "session.json"
RESULTS_NAME = "results.json"
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def ensure_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / SESSIONS_DIR).mkdir(parents=True, exist_ok=True)


def clear_all_sessions(root: Path) -> int:
    """Remove every saved scan session and active selection. Returns sessions deleted."""
    ensure_tree(root)
    removed = 0
    sessions_dir = root / SESSIONS_DIR
    if sessions_dir.is_dir():
        for child in sessions_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
                removed += 1
    (root / ACTIVE_FILE).unlink(missing_ok=True)
    return removed


def reset_scan_state(ctx: AppCtx) -> None:
    """Clear in-memory duplicate review state (groups, keepers, roll cursor)."""
    with ctx.state.lock:
        ctx.state.duplicate_groups = []
        ctx.state.group_keep = {}
        ctx.state.scan_artifact_path = None
        ctx.state.active_scan_session_id = None
        ctx.state.active_exact_scan_session_id = None
        ctx.state.active_fuzzy_scan_session_id = None
        ctx.state.fuzzy_roll_next_start = 0
        ctx.state.fuzzy_roll_total = None
        ctx.state.library_indexed_count = None
        ctx.state.pending_rescan_kind = None


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _normalize_scan_kind(scan_kind: str) -> str:
    return scan_kind if scan_kind in ("exact", "fuzzy") else "exact"


def group_scan_kind(group: dict[str, Any]) -> str:
    return _normalize_scan_kind(str(group.get("scan_kind") or "exact"))


def filter_groups_by_kind(groups: list[dict[str, Any]], scan_kind: str) -> list[dict[str, Any]]:
    sk = _normalize_scan_kind(scan_kind)
    return [g for g in groups if group_scan_kind(g) == sk]


def read_active_ids(root: Path) -> dict[str, str]:
    path = root / ACTIVE_FILE
    if not path.is_file():
        return {}
    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    out: dict[str, str] = {}
    by_kind = data.get("by_kind")
    if isinstance(by_kind, dict):
        for kind in ("exact", "fuzzy"):
            sid = by_kind.get(kind)
            if sid:
                out[kind] = str(sid)
    legacy = data.get("session_id") or data.get("active_session_id")
    if legacy and len(out) < 2:
        sid = str(legacy)
        try:
            _, _, sk, _ = load_session_groups(root, sid)
        except (OSError, ValueError, FileNotFoundError):
            sk = "exact"
        out.setdefault(_normalize_scan_kind(sk), sid)
    return out


def read_active_id(root: Path, scan_kind: str = "exact") -> str | None:
    return read_active_ids(root).get(_normalize_scan_kind(scan_kind))


def write_active_id(root: Path, session_id: str, scan_kind: str) -> None:
    ensure_tree(root)
    sk = _normalize_scan_kind(scan_kind)
    path = root / ACTIVE_FILE
    current = read_active_ids(root)
    current[sk] = session_id
    payload = {"by_kind": current, "session_id": session_id}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _session_path(root: Path, session_id: str) -> Path:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError("Invalid session id.")
    return (root / SESSIONS_DIR / session_id).resolve()


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    sid = data.get("id")
    if not sid:
        return None
    data["id"] = str(sid)
    return data


def list_sessions(root: Path) -> list[dict[str, Any]]:
    ensure_tree(root)
    sessions_dir = root / SESSIONS_DIR
    out: list[dict[str, Any]] = []
    for child in sessions_dir.iterdir():
        if not child.is_dir():
            continue
        manifest = _read_manifest(child / MANIFEST_NAME)
        if manifest:
            out.append(manifest)
    out.sort(key=lambda m: float(m.get("created_at") or 0), reverse=True)
    return out


def default_active_id(root: Path, scan_kind: str | None = None) -> str | None:
    sessions = list_sessions(root)
    if not sessions:
        return None
    if scan_kind is not None:
        sk = _normalize_scan_kind(scan_kind)
        kind_sessions = [s for s in sessions if _normalize_scan_kind(str(s.get("scan_kind") or "exact")) == sk]
        if not kind_sessions:
            return None
        stored = read_active_id(root, sk)
        ids = {str(s["id"]) for s in kind_sessions}
        if stored and stored in ids:
            return stored
        return str(kind_sessions[0]["id"])
    stored = read_active_ids(root)
    for sk in ("exact", "fuzzy"):
        sid = stored.get(sk)
        if sid:
            return sid
    return str(sessions[0]["id"])


def session_label(scan_kind: str, created_at: float) -> str:
    kind = "Fuzzy roll" if scan_kind == "fuzzy" else "Exact"
    try:
        when = datetime.fromtimestamp(created_at).strftime("%b %d, %Y %I:%M %p")
    except (OSError, OverflowError, ValueError):
        when = str(int(created_at))
    return f"{kind} · {when}"


def _group_keep_for_kind(ctx: AppCtx, scan_kind: str) -> dict[str, list[str]]:
    sk = _normalize_scan_kind(scan_kind)
    ids = {str(g["id"]) for g in ctx.state.duplicate_groups if group_scan_kind(g) == sk}
    return {gid: list(paths) for gid, paths in ctx.state.group_keep.items() if gid in ids}


def active_session_id_for_kind(ctx: AppCtx, scan_kind: str) -> str | None:
    sk = _normalize_scan_kind(scan_kind)
    with ctx.state.lock:
        if sk == "exact":
            return ctx.state.active_exact_scan_session_id
        return ctx.state.active_fuzzy_scan_session_id


def update_session_on_disk(
    root: Path,
    session_id: str,
    *,
    groups: list[dict[str, Any]],
    group_keep: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Overwrite results for an existing session and refresh manifest metadata."""
    session_dir = _session_path(root, session_id)
    manifest_path = session_dir / MANIFEST_NAME
    manifest = _read_manifest(manifest_path)
    if not manifest:
        raise FileNotFoundError(f"Unknown scan session: {session_id}")
    sk = _normalize_scan_kind(str(manifest.get("scan_kind") or "exact"))
    kind_groups = filter_groups_by_kind(groups, sk)
    gk = group_keep if group_keep is not None else {}
    results_path = session_dir / RESULTS_NAME
    scan.write_artifact(results_path, kind_groups, scan_kind=sk, group_keep=gk)
    now = time.time()
    manifest["group_count"] = len(kind_groups)
    manifest["updated_at"] = now
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest["results_path"] = str(results_path)
    return manifest


def sync_active_session_from_state(ctx: AppCtx, scan_kind: str) -> None:
    """Persist in-memory groups and keep selections for the active session of this kind."""
    sk = _normalize_scan_kind(scan_kind)
    sid = active_session_id_for_kind(ctx, sk)
    if not sid:
        return
    root = ctx.settings.user_scans_dir
    if not _session_path(root, sid).is_dir():
        return
    kind_groups = filter_groups_by_kind(list(ctx.state.duplicate_groups), sk)
    gk = _group_keep_for_kind(ctx, sk)
    update_session_on_disk(root, sid, groups=kind_groups, group_keep=gk)


def persist_session(
    root: Path,
    *,
    groups: list[dict[str, Any]],
    scan_kind: str,
    mount_udid: str | None,
    mount_path: Path | None,
    group_keep: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Write a new session directory, set it active, and return the manifest."""
    ensure_tree(root)
    sk = _normalize_scan_kind(scan_kind)
    kind_groups = filter_groups_by_kind(groups, sk)
    now = time.time()
    sid = f"{time.strftime('%Y%m%d_%H%M%S')}_{sk}_{uuid.uuid4().hex[:8]}"
    session_dir = _session_path(root, sid)
    session_dir.mkdir(parents=True, exist_ok=False)
    results_path = session_dir / RESULTS_NAME
    gk = group_keep if group_keep is not None else {}
    scan.write_artifact(results_path, kind_groups, scan_kind=sk, group_keep=gk)
    manifest: dict[str, Any] = {
        "id": sid,
        "label": session_label(sk, now),
        "scan_kind": sk,
        "created_at": now,
        "updated_at": now,
        "group_count": len(kind_groups),
        "mount_udid": mount_udid,
        "mount_path": str(mount_path) if mount_path else None,
        "results_file": RESULTS_NAME,
    }
    (session_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    write_active_id(root, sid, sk)
    manifest["results_path"] = str(results_path)
    return manifest


def load_session_groups(
    root: Path, session_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]], str, dict[str, list[str]]]:
    """Return (manifest, groups, scan_kind, saved_group_keep)."""
    session_dir = _session_path(root, session_id)
    manifest_path = session_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Unknown scan session: {session_id}")
    manifest = _read_manifest(manifest_path)
    if not manifest:
        raise FileNotFoundError(f"Invalid session manifest: {session_id}")
    results_path = session_dir / RESULTS_NAME
    if not results_path.is_file():
        raise FileNotFoundError(f"Missing results for session: {session_id}")
    payload = _read_json(results_path)
    groups = list(payload.get("groups") or [])
    sk = str(payload.get("scan_kind") or manifest.get("scan_kind") or "exact")
    saved_keep: dict[str, list[str]] = {}
    raw_gk = payload.get("group_keep")
    if isinstance(raw_gk, dict):
        for gid, paths in raw_gk.items():
            if isinstance(paths, list):
                saved_keep[str(gid)] = [str(p) for p in paths]
    return manifest, groups, sk, saved_keep


def _apply_auto_groups(ctx: AppCtx, *, skip_group_ids: set[str] | None = None) -> None:
    root = ctx.state.mount_path
    face = ctx.settings.duplicates_auto_face_eye
    max_img = ctx.settings.duplicates_face_eye_max_images
    skip = skip_group_ids or set()
    for g in ctx.state.duplicate_groups:
        paths = list(g.get("paths") or [])
        if not paths:
            continue
        gid = str(g["id"])
        if gid in skip:
            continue
        if str(g.get("scan_kind") or "exact") == "fuzzy":
            kps = auto_best.pick_fuzzy_keepers_by_eye_count(paths)
        else:
            keep = auto_best.pick_recommended(paths, face_eye=face, face_eye_max_images=max_img)
            kps = [keep] if keep else [paths[0]]
        g["recommendedKeeps"] = list(kps)
        g["recommendedKeep"] = kps[0]
        ctx.state.group_keep[gid] = list(kps)


def _prune_group_keep(ctx: AppCtx, valid_ids: set[str]) -> None:
    for gid in list(ctx.state.group_keep.keys()):
        if gid not in valid_ids:
            del ctx.state.group_keep[gid]


def apply_session_to_state(ctx: AppCtx, session_id: str) -> dict[str, Any]:
    """Load a saved session into in-memory duplicate review state (merges by scan kind)."""
    root = ctx.settings.user_scans_dir
    manifest, groups, scan_kind, saved_keep = load_session_groups(root, session_id)
    sk = _normalize_scan_kind(scan_kind)
    session_dir = _session_path(root, session_id)
    results_path = session_dir / RESULTS_NAME

    with ctx.state.lock:
        other = [g for g in ctx.state.duplicate_groups if group_scan_kind(g) != sk]
        ctx.state.duplicate_groups = other + groups
        valid_ids = {str(g["id"]) for g in ctx.state.duplicate_groups}
        _prune_group_keep(ctx, valid_ids)
        for gid, paths in saved_keep.items():
            if gid in valid_ids:
                ctx.state.group_keep[gid] = list(paths)
        if sk == "exact":
            ctx.state.active_exact_scan_session_id = session_id
        else:
            ctx.state.active_fuzzy_scan_session_id = session_id
        ctx.state.active_scan_session_id = session_id
        ctx.state.scan_artifact_path = results_path

    _apply_auto_groups(ctx, skip_group_ids=set(saved_keep.keys()))
    write_active_id(root, session_id, sk)

    if ctx.state.duplicate_groups:
        ctx.state.set_phase(Phase.reviewing)
    elif ctx.state.mount_path:
        ctx.state.set_phase(Phase.mounted)
    return manifest


def bootstrap_active_session(ctx: AppCtx) -> list[str]:
    """On startup: load the default saved session for each scan kind, if any."""
    root = ctx.settings.user_scans_dir
    ensure_tree(root)
    loaded: list[str] = []
    for sk in ("exact", "fuzzy"):
        sid = default_active_id(root, sk)
        if not sid:
            continue
        try:
            apply_session_to_state(ctx, sid)
            ctx.state.append_activity(
                f"SCAN SESSION | loaded default {sk!r} session {sid!r} from user_scans/"
            )
            loaded.append(sid)
        except (OSError, ValueError, FileNotFoundError) as e:
            ctx.state.append_activity(f"SCAN SESSION | could not load {sk!r} session {sid!r}: {e}")
    return loaded
