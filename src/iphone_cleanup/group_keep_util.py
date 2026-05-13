"""Resolve which paths in a duplicate group are marked to keep (supports multiple keepers)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from iphone_cleanup.app_context import AppCtx


def paths_in_group(g: dict[str, Any]) -> list[str]:
    return [str(x) for x in g.get("paths") or []]


def keep_paths_set(ctx: AppCtx, g: dict[str, Any]) -> set[str]:
    """Paths the user (or auto rules) intend to keep; always non-empty when the group has paths."""
    gid = str(g["id"])
    path_list = paths_in_group(g)
    if not path_list:
        return set()
    path_set = set(path_list)
    raw = ctx.state.group_keep.get(gid)
    out: set[str] = set()
    if isinstance(raw, list):
        out = {str(x) for x in raw if str(x) in path_set}
    elif isinstance(raw, str) and raw and raw in path_set:
        out = {raw}
    if not out:
        rklist = g.get("recommendedKeeps")
        if isinstance(rklist, list) and rklist:
            for x in rklist:
                sx = str(x)
                if sx in path_set:
                    out.add(sx)
        if not out:
            rk = str(g.get("recommendedKeep") or "")
            if rk in path_set:
                out.add(rk)
        if not out:
            out.add(path_list[0])
    return out
