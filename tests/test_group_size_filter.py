"""Tests for duplicate group size filtering."""

from iphone_cleanup.group_size_filter import (
    filter_groups_by_size,
    group_matches_size_filter,
    normalize_size_filter,
    size_filter_counts,
)


def _g(n: int, gid: str = "g") -> dict:
    return {"id": gid, "paths": [f"/p{i}.jpg" for i in range(n)]}


def test_normalize_size_filter():
    assert normalize_size_filter(None) == "all"
    assert normalize_size_filter("5+") == "5plus"
    assert normalize_size_filter("3") == "3"


def test_filter_and_counts():
    groups = [_g(2, "a"), _g(2, "b"), _g(3, "c"), _g(5, "d")]
    assert len(filter_groups_by_size(groups, "2")) == 2
    assert len(filter_groups_by_size(groups, "3")) == 1
    assert len(filter_groups_by_size(groups, "5plus")) == 1
    counts = size_filter_counts(groups)
    assert counts == {"all": 4, "2": 2, "3": 1, "4": 0, "5plus": 1}


def test_group_matches():
    assert group_matches_size_filter(_g(2), "2")
    assert not group_matches_size_filter(_g(3), "2")
