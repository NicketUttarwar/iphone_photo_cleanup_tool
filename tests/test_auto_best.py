"""Tests for iphone_cleanup.auto_best."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from iphone_cleanup import auto_best


def test_pick_recommended_empty():
    assert auto_best.pick_recommended([], face_eye=False, face_eye_max_images=3) == ""


def test_pick_recommended_prefers_larger_image(tmp_path: Path):
    small = tmp_path / "s.jpg"
    big = tmp_path / "b.jpg"
    Image.new("RGB", (10, 10), "white").save(small, "JPEG")
    Image.new("RGB", (200, 200), "white").save(big, "JPEG")
    pick = auto_best.pick_recommended([str(small), str(big)], face_eye=False, face_eye_max_images=0)
    assert Path(pick).resolve() == big.resolve()


def test_sharpness_score_non_image(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"not an image")
    assert auto_best._sharpness_score(p) == 0.0


def test_exif_capture_ts_fallback_mtime(tmp_path: Path):
    p = tmp_path / "n.jpg"
    Image.new("RGB", (4, 4), "blue").save(p, "JPEG")
    ts = auto_best._exif_capture_ts(p)
    assert ts > 0
