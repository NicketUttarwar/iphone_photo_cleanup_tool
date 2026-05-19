"""Palette grid signatures and multi-signal adjacent fuzzy linking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import imagehash
import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Callable

try:
    from scipy.stats import wasserstein_distance as _wasserstein_distance

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

HS_BINS = 32
HS_BIN_COUNT = HS_BINS
V_MIN = 0.12
V_MAX = 0.95
ACTIVE_BIN_FRAC = 0.01


def clamp_grid_side(side: int) -> int:
    return max(4, min(16, int(side)))


def scipy_available() -> bool:
    return _HAS_SCIPY


@dataclass(frozen=True)
class PaletteSignature:
    """Global HS histogram + per-cell dominant bins on a square grid."""

    histogram: np.ndarray  # shape (HS_BIN_COUNT,), float32 normalized
    active_color_count: int
    grid: np.ndarray  # shape (grid_side * grid_side,), uint8 bin ids 0..63
    grid_side: int

    @property
    def n_cells(self) -> int:
        return int(self.grid_side * self.grid_side)

    def serialize(self) -> str:
        h = self.histogram.astype(np.float32).tobytes()
        g = self.grid.astype(np.uint8).tobytes()
        return (h + g).hex()

    @staticmethod
    def deserialize(raw: str, *, grid_side: int) -> PaletteSignature | None:
        try:
            data = bytes.fromhex(raw)
            n_cells = grid_side * grid_side
            h_bytes = HS_BIN_COUNT * 4
            if len(data) < h_bytes + n_cells:
                return None
            histogram = np.frombuffer(data[:h_bytes], dtype=np.float32).copy()
            grid = np.frombuffer(data[h_bytes : h_bytes + n_cells], dtype=np.uint8).copy()
            if histogram.shape[0] != HS_BIN_COUNT or grid.shape[0] != n_cells:
                return None
            active = int(np.sum(histogram >= ACTIVE_BIN_FRAC))
            return PaletteSignature(
                histogram=histogram,
                active_color_count=active,
                grid=grid,
                grid_side=grid_side,
            )
        except Exception:
            return None


@dataclass(frozen=True)
class FuzzyMatchConfig:
    phash_max_dim: int
    max_adjacent_hamming: int
    colorhash_max_hamming: int = 10
    max_adjacent_gap_sec: float = 120.0
    palette_enabled: bool = True
    palette_max_distance: float = 0.32
    palette_max_color_count_delta: int = 4
    palette_min_grid_agreement: float = 0.55
    palette_grid: int = 16
    fast_path_enabled: bool = True
    grid_exact_match_min: int = 200

    @property
    def grid_side(self) -> int:
        return clamp_grid_side(self.palette_grid)

    @property
    def n_cells(self) -> int:
        s = self.grid_side
        return s * s

    def scaled_exact_match_min(self) -> int:
        """Scale default 200/256 threshold when grid_side != 16."""
        base_cells = 256
        return max(2, int(round(self.grid_exact_match_min * self.n_cells / base_cells)))


def _rgb_to_hs_bins(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """rgb uint8 (H,W,3) -> hue [0,1), sat [0,1], val [0,1]."""
    r = rgb[..., 0].astype(np.float32) / 255.0
    g = rgb[..., 1].astype(np.float32) / 255.0
    b = rgb[..., 2].astype(np.float32) / 255.0
    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin
    hue = np.zeros_like(cmax)
    mask = delta > 1e-6
    rc = np.where(mask, (g - b) / np.maximum(delta, 1e-6), 0.0)
    gc = np.where(mask, (b - r) / np.maximum(delta, 1e-6), 0.0)
    bc = np.where(mask, (r - g) / np.maximum(delta, 1e-6), 0.0)
    h = np.where(cmax == r, rc % 6.0, 0.0)
    h = np.where((cmax == g) & (cmax != r), gc + 2.0, h)
    h = np.where((cmax == b) & (cmax != r) & (cmax != g), bc + 4.0, h)
    hue = np.where(mask, (h / 6.0) % 1.0, 0.0)
    sat = np.where(cmax > 1e-6, delta / np.maximum(cmax, 1e-6), 0.0)
    return hue, sat, cmax


def compute_palette_from_rgb(rgb: np.ndarray, grid_side: int) -> PaletteSignature:
    grid_side = clamp_grid_side(grid_side)
    hue, sat, val = _rgb_to_hs_bins(rgb)
    scene = (val >= V_MIN) & (val <= V_MAX)
    hue_bin = np.clip((hue * 8).astype(np.int32), 0, 7)
    sat_bin = np.clip((sat * 4).astype(np.int32), 0, 3)
    bin_id_full = (hue_bin * 4 + sat_bin).astype(np.uint8)

    hist = np.zeros(HS_BIN_COUNT, dtype=np.float32)
    if np.any(scene):
        ids = bin_id_full[scene].ravel()
        for b in range(HS_BIN_COUNT):
            hist[b] = float(np.sum(ids == b))
        total = float(hist.sum())
        if total > 0:
            hist /= total
    active = int(np.sum(hist >= ACTIVE_BIN_FRAC))

    h, w = rgb.shape[:2]
    cell_h = max(1, h // grid_side)
    cell_w = max(1, w // grid_side)
    cells: list[int] = []
    for gy in range(grid_side):
        for gx in range(grid_side):
            y0, y1 = gy * cell_h, min((gy + 1) * cell_h, h)
            x0, x1 = gx * cell_w, min((gx + 1) * cell_w, w)
            patch_scene = scene[y0:y1, x0:x1]
            patch_bins = bin_id_full[y0:y1, x0:x1]
            if np.any(patch_scene):
                vals, counts = np.unique(patch_bins[patch_scene], return_counts=True)
                cells.append(int(vals[int(np.argmax(counts))]))
            else:
                cells.append(0)
    grid = np.array(cells, dtype=np.uint8)
    return PaletteSignature(
        histogram=hist,
        active_color_count=active,
        grid=grid,
        grid_side=grid_side,
    )


def compute_palette_from_image(im: Image.Image, grid_side: int) -> PaletteSignature:
    rgb = np.asarray(im.convert("RGB"))
    return compute_palette_from_rgb(rgb, grid_side)


def exact_grid_matches(a: PaletteSignature, b: PaletteSignature) -> int:
    n = min(a.n_cells, b.n_cells)
    if n == 0:
        return 0
    return int(np.sum(a.grid[:n] == b.grid[:n]))


def histogram_similarity(a: PaletteSignature, b: PaletteSignature) -> float:
    ha = a.histogram.astype(np.float64)
    hb = b.histogram.astype(np.float64)
    if not np.isfinite(ha).all() or not np.isfinite(hb).all():
        return 0.0
    sum_a = float(ha.sum())
    sum_b = float(hb.sum())
    if sum_a <= 0 and sum_b <= 0:
        return 1.0
    if sum_a <= 0 or sum_b <= 0:
        return 0.0
    ha = ha / sum_a
    hb = hb / sum_b
    if _HAS_SCIPY:
        support = np.arange(HS_BIN_COUNT, dtype=np.float64)
        dist = float(_wasserstein_distance(support, support, ha, hb))
        max_dist = float(HS_BIN_COUNT - 1)
        return max(0.0, 1.0 - dist / max_dist)
    return float(np.minimum(ha, hb).sum())


def palette_slow_distance(
    a: PaletteSignature,
    b: PaletteSignature,
    *,
    max_color_count_delta: int,
    min_grid_agreement: float,
) -> tuple[float, bool]:
    """Combined distance (0=identical) and whether slow-path palette rules pass."""
    exact = exact_grid_matches(a, b)
    n = max(a.n_cells, b.n_cells, 1)
    grid_agree = exact / n
    if grid_agree < min_grid_agreement:
        return 1.0, False
    count_delta = abs(a.active_color_count - b.active_color_count)
    denom = max(a.active_color_count, b.active_color_count, 1)
    count_sim = 1.0 - min(count_delta, denom) / denom
    if count_delta > max_color_count_delta:
        return 1.0, False
    hist_sim = histogram_similarity(a, b)
    score = 0.45 * hist_sim + 0.25 * count_sim + 0.30 * grid_agree
    distance = 1.0 - score
    return distance, True


@dataclass(frozen=True)
class FuzzyFeatures:
    phash: imagehash.ImageHash | None
    colorhash: imagehash.ImageHash | None
    palette: PaletteSignature | None
    capture_ts: float | None


def extract_fuzzy_features(
    im: Image.Image,
    *,
    max_dim: int,
    grid_side: int,
    capture_ts: float | None = None,
) -> FuzzyFeatures:
    thumb = im.convert("RGB")
    thumb.thumbnail((max_dim, max_dim))
    ph = imagehash.phash(thumb)
    ch = imagehash.colorhash(thumb)
    pal = compute_palette_from_image(thumb, grid_side)
    return FuzzyFeatures(phash=ph, colorhash=ch, palette=pal, capture_ts=capture_ts)


def extract_fuzzy_features_from_path(
    path: Any,
    *,
    max_dim: int,
    grid_side: int,
    capture_ts: float | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> FuzzyFeatures | None:
    from pathlib import Path

    try:
        if cancel_check:
            cancel_check()
        with Image.open(Path(path)) as im:
            if cancel_check:
                cancel_check()
            return extract_fuzzy_features(
                im, max_dim=max_dim, grid_side=grid_side, capture_ts=capture_ts
            )
    except Exception:
        return None


def time_gap_ok(ts_a: float | None, ts_b: float | None, max_gap_sec: float) -> bool:
    if max_gap_sec <= 0:
        return True
    if ts_a is None or ts_b is None:
        return True
    return abs(ts_a - ts_b) <= max_gap_sec


def adjacent_fuzzy_link(
    a: FuzzyFeatures,
    b: FuzzyFeatures,
    cfg: FuzzyMatchConfig,
) -> tuple[bool, str, str]:
    """
    Decide if adjacent photos link.

    Returns (linked, fuzzy_match_reason, fuzzy_link_strength).
    """
    if not time_gap_ok(a.capture_ts, b.capture_ts, cfg.max_adjacent_gap_sec):
        return False, "", ""

    if (
        cfg.fast_path_enabled
        and cfg.palette_enabled
        and a.palette is not None
        and b.palette is not None
    ):
        exact = exact_grid_matches(a.palette, b.palette)
        if exact >= cfg.scaled_exact_match_min():
            return True, "grid_exact", "strong"

    reasons: list[str] = []

    if a.phash is not None and b.phash is not None:
        if (a.phash - b.phash) <= cfg.max_adjacent_hamming:
            reasons.append("visual")

    if a.colorhash is not None and b.colorhash is not None:
        if (a.colorhash - b.colorhash) <= cfg.colorhash_max_hamming:
            reasons.append("color")

    if cfg.palette_enabled and a.palette is not None and b.palette is not None:
        dist, passes = palette_slow_distance(
            a.palette,
            b.palette,
            max_color_count_delta=cfg.palette_max_color_count_delta,
            min_grid_agreement=cfg.palette_min_grid_agreement,
        )
        if passes and dist <= cfg.palette_max_distance:
            reasons.append("palette")

    if not reasons:
        return False, "", ""

    if len(reasons) >= 2:
        return True, "mixed", "strong"
    reason = reasons[0]
    strength = "strong" if reason in ("visual", "color", "grid_exact") else "moderate"
    if reason == "palette":
        strength = "moderate"
    elif reason in ("visual", "color"):
        strength = "strong"
    return True, reason, strength


def vote_chain_metadata(link_reasons: list[str], strengths: list[str]) -> tuple[str, str]:
    if not link_reasons:
        return "mixed", "moderate"
    from collections import Counter

    rc = Counter(link_reasons)
    reason = rc.most_common(1)[0][0]
    sc = Counter(strengths)
    strength = sc.most_common(1)[0][0]
    if sc.get("strong", 0) >= max(1, len(strengths) // 2):
        strength = "strong"
    return reason, strength
