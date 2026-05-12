"""Tests for iphone_cleanup.documents (tag/path heuristics, quarantine, undo)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PIL import Image, ImageDraw

from iphone_cleanup import documents


def test_path_hint_finds_receipt_jpeg(tmp_path: Path) -> None:
    mount = tmp_path / "m"
    dcim = mount / "DCIM"
    dcim.mkdir(parents=True)
    p = dcim / "receipt_store.jpg"
    Image.new("RGB", (32, 32), "red").save(p, "JPEG")
    found = documents.iter_document_paths(mount, "all", include_visual_fallback=False)
    assert p.resolve() in [x.resolve() for x in found]


def test_older_than_90d_filters_by_mtime(tmp_path: Path) -> None:
    mount = tmp_path / "m"
    dcim = mount / "DCIM"
    dcim.mkdir(parents=True)
    old = dcim / "scan_old.jpg"
    Image.new("RGB", (20, 20), "blue").save(old, "JPEG")
    t_old = time.time() - 120 * 86400
    os.utime(old, (t_old, t_old))
    new = dcim / "scan_new.jpg"
    Image.new("RGB", (20, 20), "green").save(new, "JPEG")
    found = documents.iter_document_paths(
        mount,
        "older_than_90d",
        include_visual_fallback=False,
    )
    rel = {x.resolve() for x in found}
    assert old.resolve() in rel
    assert new.resolve() not in rel


def test_visual_fallback_detects_texty_white_page(tmp_path: Path) -> None:
    mount = tmp_path / "m"
    f = mount / "IMG_0001.JPG"
    f.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (160, 160), (252, 252, 252))
    d = ImageDraw.Draw(im)
    d.rectangle([20, 70, 140, 88], fill=(25, 25, 25))
    im.save(f, "JPEG")
    assert documents.iter_document_paths(mount, "all", include_visual_fallback=False) == []
    assert len(documents.iter_document_paths(mount, "all", include_visual_fallback=True)) == 1


def test_quarantine_undo_roundtrip(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    data = tmp_path / "data"
    mount.mkdir()
    p = mount / "note" / "receipt.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"abc")
    res = documents.quarantine_and_remove([p], mount, data, batch_id="tbatch")
    assert res["copied_then_removed"] == 1
    assert not p.exists()
    u = documents.undo_batch("tbatch", mount, data)
    assert len(u["errors"]) == 0
    assert p.read_bytes() == b"abc"
    assert not (documents.document_quarantine_root(data) / "tbatch").exists()


def test_finalize_drops_quarantine(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    data = tmp_path / "data"
    mount.mkdir()
    p = mount / "receipt_small.jpg"
    Image.new("RGB", (40, 40), "white").save(p, "JPEG")
    res = documents.quarantine_and_remove([p], mount, data, batch_id="b2")
    assert res["batch_id"] == "b2"
    documents.finalize_batch("b2", data)
    assert not (documents.document_quarantine_root(data) / "b2").exists()
