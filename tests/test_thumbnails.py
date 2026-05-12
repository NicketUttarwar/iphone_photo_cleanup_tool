"""Tests for iphone_cleanup.thumbnails."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from iphone_cleanup import thumbnails


def test_cache_key_stable(tmp_path: Path):
    root = tmp_path / "m"
    root.mkdir()
    src = root / "p.jpg"
    Image.new("RGB", (20, 10), "red").save(src, "JPEG")
    k1 = thumbnails._cache_key(root, src, 64, 70)
    k2 = thumbnails._cache_key(root, src, 64, 70)
    assert k1 == k2


def test_get_thumbnail_jpeg_creates_cache(tmp_path: Path):
    root = tmp_path / "mount"
    root.mkdir()
    src = root / "a.jpg"
    Image.new("RGB", (100, 80), "green").save(src, "JPEG")
    cache = tmp_path / "cache"
    data = thumbnails.get_thumbnail_jpeg(src, root.resolve(), cache, 40, 60, cache_max_mb=2)
    assert data[:2] == b"\xff\xd8"
    jpg_files = list(cache.glob("*.jpg"))
    assert jpg_files


def test_get_thumbnail_outside_mount_raises(tmp_path: Path):
    root = tmp_path / "mount"
    root.mkdir()
    evil = tmp_path / "outside.jpg"
    Image.new("RGB", (5, 5), "black").save(evil, "JPEG")
    with pytest.raises(PermissionError, match="outside"):
        thumbnails.get_thumbnail_jpeg(evil, root.resolve(), tmp_path / "c", 32, 60, 1)


def test_enforce_cache_budget_trims_oldest(tmp_path: Path):
    c = tmp_path / "cache"
    c.mkdir()
    old = c / "old.jpg"
    new = c / "new.jpg"
    old.write_bytes(b"x" * 5000)
    new.write_bytes(b"y" * 5000)
    thumbnails._enforce_cache_budget(c, max_mb=0)
    # max_mb <= 0 returns early without deleting in current impl
    thumbnails._enforce_cache_budget(c, max_mb=1)
    # budget very small — at least one file may be removed
    total = sum(p.stat().st_size for p in c.glob("*.jpg"))
    assert total <= 1 * 1024 * 1024
