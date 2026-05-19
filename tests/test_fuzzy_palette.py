"""Tests for iphone_cleanup.fuzzy_palette."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from iphone_cleanup.fuzzy_palette import (
    FuzzyFeatures,
    FuzzyMatchConfig,
    PaletteSignature,
    adjacent_fuzzy_link,
    clamp_grid_side,
    compute_palette_from_rgb,
    exact_grid_matches,
    extract_fuzzy_features,
    histogram_similarity,
)


def _solid_rgb(w: int, h: int, color: tuple[int, int, int]) -> np.ndarray:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = color
    return arr


def test_clamp_grid_side():
    assert clamp_grid_side(3) == 4
    assert clamp_grid_side(16) == 16
    assert clamp_grid_side(99) == 16


def test_palette_serialize_roundtrip():
    rgb = _solid_rgb(64, 64, (90, 120, 200))
    sig = compute_palette_from_rgb(rgb, 16)
    raw = sig.serialize()
    back = PaletteSignature.deserialize(raw, grid_side=16)
    assert back is not None
    assert back.n_cells == 256
    assert np.allclose(back.histogram, sig.histogram)


def test_exact_grid_matches_identical():
    rgb = _solid_rgb(128, 128, (200, 100, 50))
    a = compute_palette_from_rgb(rgb, 16)
    b = compute_palette_from_rgb(rgb, 16)
    assert exact_grid_matches(a, b) == 256


def test_fast_path_links_identical_scenes():
    cfg = FuzzyMatchConfig(
        phash_max_dim=96,
        max_adjacent_hamming=2,
        fast_path_enabled=True,
        grid_exact_match_min=200,
    )
    rgb = _solid_rgb(128, 128, (80, 140, 200))
    im = Image.fromarray(rgb)
    fa = extract_fuzzy_features(im, max_dim=96, grid_side=16, capture_ts=100.0)
    fb = extract_fuzzy_features(im, max_dim=96, grid_side=16, capture_ts=101.0)
    linked, reason, strength = adjacent_fuzzy_link(fa, fb, cfg)
    assert linked is True
    assert reason == "grid_exact"
    assert strength == "strong"


def test_time_gap_blocks_link():
    cfg = FuzzyMatchConfig(
        phash_max_dim=96,
        max_adjacent_hamming=14,
        max_adjacent_gap_sec=60,
    )
    rgb = _solid_rgb(64, 64, (90, 120, 200))
    pal = compute_palette_from_rgb(rgb, 16)
    fa = FuzzyFeatures(phash=None, colorhash=None, palette=pal, capture_ts=0.0)
    fb = FuzzyFeatures(phash=None, colorhash=None, palette=pal, capture_ts=500.0)
    linked, _, _ = adjacent_fuzzy_link(fa, fb, cfg)
    assert linked is False


def test_histogram_similarity_empty_histograms():
    """All-black / no-scene images produce zero histograms; must not raise scipy weight errors."""
    black = compute_palette_from_rgb(_solid_rgb(64, 64, (0, 0, 0)), 16)
    red = compute_palette_from_rgb(_solid_rgb(64, 64, (200, 50, 50)), 16)
    assert histogram_similarity(black, black) == 1.0
    assert histogram_similarity(black, red) == 0.0


def test_contrasting_colors_low_exact_matches():
    a = compute_palette_from_rgb(_solid_rgb(64, 64, (200, 50, 50)), 16)
    b = compute_palette_from_rgb(_solid_rgb(64, 64, (50, 50, 200)), 16)
    assert exact_grid_matches(a, b) < 50
