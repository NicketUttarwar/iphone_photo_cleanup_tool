"""Filter duplicate groups by how many images are in each set."""

from __future__ import annotations

from typing import Any, Literal

GroupSizeFilter = Literal["all", "2", "3", "4", "5plus"]

VALID_SIZE_FILTERS: frozenset[str] = frozenset({"all", "2", "3", "4", "5plus"})


def normalize_size_filter(raw: str | None) -> GroupSizeFilter:
    if not raw or raw == "all":
        return "all"
    key = str(raw).strip().lower()
    if key in ("5+", "5_plus", "5plus"):
        return "5plus"
    if key in VALID_SIZE_FILTERS:
        return key  # type: ignore[return-value]
    raise ValueError(f"size_filter must be one of: {', '.join(sorted(VALID_SIZE_FILTERS))}")


def group_path_count(group: dict[str, Any]) -> int:
    return len(group.get("paths") or [])


def group_matches_size_filter(group: dict[str, Any], size_filter: GroupSizeFilter) -> bool:
    if size_filter == "all":
        return True
    n = group_path_count(group)
    if size_filter == "2":
        return n == 2
    if size_filter == "3":
        return n == 3
    if size_filter == "4":
        return n == 4
    if size_filter == "5plus":
        return n >= 5
    return False


def filter_groups_by_size(
    groups: list[dict[str, Any]],
    size_filter: GroupSizeFilter,
) -> list[dict[str, Any]]:
    if size_filter == "all":
        return list(groups)
    return [g for g in groups if group_matches_size_filter(g, size_filter)]


def size_filter_counts(groups: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"all": len(groups), "2": 0, "3": 0, "4": 0, "5plus": 0}
    for g in groups:
        n = group_path_count(g)
        if n == 2:
            counts["2"] += 1
        elif n == 3:
            counts["3"] += 1
        elif n == 4:
            counts["4"] += 1
        elif n >= 5:
            counts["5plus"] += 1
    return counts


def size_filter_label(size_filter: GroupSizeFilter) -> str:
    if size_filter == "all":
        return "all sets"
    if size_filter == "5plus":
        return "5+ photo sets"
    return f"{size_filter}-photo sets"
