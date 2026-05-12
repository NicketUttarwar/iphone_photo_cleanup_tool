"""Pick recommended keepers: cheap signals + optional face/eye assist."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from PIL.ExifTags import TAGS

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass


def _sharpness_score(path: Path, max_edge: int = 256) -> float:
    try:
        with Image.open(path) as im:
            im = im.convert("L")
            im.thumbnail((max_edge, max_edge))
            arr = np.asarray(im, dtype=np.float64)
        gx = np.diff(arr, axis=1)
        gy = np.diff(arr, axis=0)
        mag = np.hypot(gx[:-1, :], gy[:, :-1])
        return float(np.var(mag))
    except Exception:
        return 0.0


def _pixel_score(path: Path) -> int:
    try:
        with Image.open(path) as im:
            w, h = im.size
        return int(w * h)
    except Exception:
        return 0


def _exif_capture_ts(path: Path) -> float:
    """Best-effort EXIF capture time as epoch seconds; falls back to mtime."""
    try:
        st_mtime = path.stat().st_mtime
    except OSError:
        st_mtime = 0.0
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            if not ex:
                return float(st_mtime)
            for k, v in ex.items():
                name = TAGS.get(k, k)
                if name not in ("DateTimeOriginal", "DateTime"):
                    continue
                if not isinstance(v, str):
                    continue
                try:
                    dt = datetime.strptime(v.strip(), "%Y:%m:%d %H:%M:%S")
                    return dt.timestamp()
                except ValueError:
                    continue
    except Exception:
        pass
    return float(st_mtime)


def _eye_face_score(path: Path, max_edge: int = 256) -> float | None:
    try:
        import mediapipe as mp  # type: ignore
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_edge, max_edge))
            arr = np.asarray(im)
    except Exception:
        return None
    try:
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=2,
            refine_landmarks=True,
            min_detection_confidence=0.5,
        )
        res = face_mesh.process(arr)
        face_mesh.close()
        if not res.multi_face_landmarks:
            return 0.0
        lm = res.multi_face_landmarks[0].landmark

        def vdist(a: int, b: int) -> float:
            pa, pb = lm[a], lm[b]
            return abs(pa.y - pb.y)

        left_open = vdist(159, 145)
        right_open = vdist(386, 374)
        score = float(left_open + right_open)
        return score
    except Exception:
        return 0.0


def pick_recommended(
    paths: list[str],
    *,
    face_eye: bool,
    face_eye_max_images: int,
) -> str:
    """
    Rank candidates: optional eye assist (first N paths), then sharpness, pixels,
    file size, EXIF/mtime capture time (prefer newer when tied above).
    """
    if not paths:
        return ""
    scored: list[tuple[float, float, float, int, float, float, str]] = []
    for i, p in enumerate(paths):
        path = Path(p)
        sharp = _sharpness_score(path)
        px = _pixel_score(path)
        eye = 0.0
        if face_eye and i < face_eye_max_images:
            es = _eye_face_score(path)
            eye = float(es) if es is not None else 0.0
        try:
            sz = path.stat().st_size if path.exists() else 0
        except OSError:
            sz = 0
        cap_ts = _exif_capture_ts(path)
        try:
            mtime = path.stat().st_mtime if path.exists() else 0.0
        except OSError:
            mtime = 0.0
        scored.append((eye, sharp, float(px), int(sz), cap_ts, mtime, p))
    scored.sort(
        key=lambda t: (t[0], t[1], t[2], t[3], t[4], t[5], t[6]),
        reverse=True,
    )
    return scored[0][6]
