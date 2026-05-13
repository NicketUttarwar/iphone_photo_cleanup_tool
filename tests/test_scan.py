"""Tests for iphone_cleanup.scan."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from PIL import Image

from iphone_cleanup import scan


def _make_dup_pair(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (32, 24), color=(120, 40, 200))
    for name in ("one.jpg", "two.jpg"):
        img.save(dir_path / name, "JPEG", quality=95)


def test_finalize_duplicate_groups_sizes(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"xx")
    b.write_bytes(b"x")
    groups = scan.finalize_duplicate_groups([[a, b]])
    assert len(groups) == 1
    g = groups[0]
    assert len(g["paths"]) == 2
    assert g["bytesSavedIfOneKept"] >= 0
    assert Path(g["recommendedKeep"]).name in ("a.bin", "b.bin")
    assert g.get("scan_kind") == "exact"
    assert isinstance(g.get("recommendedKeeps"), list)


def test_scan_duplicates_finds_visual_dupes(tmp_path: Path):
    d = tmp_path / "lib"
    _make_dup_pair(d)
    groups = scan.scan_duplicates(d, phash_threshold=6)
    assert len(groups) == 1
    assert len(groups[0]["paths"]) == 2
    assert groups[0].get("scan_kind") == "exact"


def test_fuzzy_roll_groups_similar_adjacent(tmp_path: Path):
    d = tmp_path / "roll"
    d.mkdir()
    img = Image.new("RGB", (48, 36), color=(90, 120, 200))
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        img.save(d / name, "JPEG", quality=93)
    groups = scan.scan_fuzzy_roll_bursts(
        d,
        phash_max_dim=96,
        max_adjacent_hamming=14,
        progress_callback=None,
        cancel_event=None,
    )
    assert len(groups) >= 1
    assert groups[0].get("scan_kind") == "fuzzy"
    assert len(groups[0]["paths"]) >= 2


def test_scan_cancelled_immediately(tmp_path: Path):
    d = tmp_path / "lib"
    _make_dup_pair(d)
    ev = threading.Event()
    ev.set()
    with pytest.raises(scan.ScanCancelled) as ei:
        scan.scan_duplicates(d, phash_threshold=6, cancel_event=ev)
    assert isinstance(ei.value.partial_groups, list)


def test_write_load_artifact_roundtrip(tmp_path: Path):
    p = tmp_path / "out.json"
    groups = [{"id": "g1", "paths": ["/a", "/b"]}]
    scan.write_artifact(p, groups)
    loaded = scan.load_artifact(p)
    assert loaded == groups


def test_load_artifact_empty_groups_key(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({}), encoding="utf-8")
    assert scan.load_artifact(p) == []


def test_walk_cancel_during_rglob(tmp_path: Path):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(300):
        (root / f"{i}.jpg").write_bytes(b"")
    ev = threading.Event()
    ev.set()
    with pytest.raises(scan.ScanCancelled):
        scan.scan_duplicates(root, phash_threshold=6, cancel_event=ev)


def test_walk_images_filters_extensions(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"")
    (root / "b.txt").write_text("no", encoding="utf-8")
    files = scan._walk_images(root)
    assert len(files) == 1
    assert files[0].suffix.lower() == ".jpg"
