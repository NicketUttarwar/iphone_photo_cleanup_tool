"""Load merged YAML settings from config files only (no environment-based app config)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, val in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Config root must be a mapping: {path}")
    return data


def load_merged_settings(defaults_path: Path, local_path: Path | None) -> dict[str, Any]:
    merged = load_yaml(defaults_path)
    if local_path is not None and local_path.is_file():
        merged = deep_merge(merged, load_yaml(local_path))
    return merged


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    server_host: str
    server_port: int
    data_dir: Path
    logs_dir: Path
    thumbnail_cache_dir: Path
    scan_artifacts_dir: Path
    mount_point: Path
    delete_chunk_size: int
    ideviceinfo: str | None
    idevice_id: str | None
    ifuse: str | None
    ui_open_browser: bool
    thumbnail_max_edge: int
    thumbnail_jpeg_quality: int
    thumbnail_cache_max_mb: int
    max_concurrent_thumbnails: int
    sse_poll_interval_ms: int
    duplicates_auto_face_eye: bool
    duplicates_face_eye_max_images: int
    phash_threshold: int
    fuzzy_phash_max_hamming: int
    fuzzy_phash_max_dim: int
    fuzzy_roll_batch_size: int
    log_level: str

    @staticmethod
    def from_dict(repo_root: Path, data: dict[str, Any]) -> Settings:
        server = data.get("server") or {}
        paths = data.get("paths") or {}
        tools = data.get("tools") or {}
        ui = data.get("ui") or {}
        dup = data.get("duplicates") or {}
        auto = dup.get("auto_best") or {}
        logging = data.get("logging") or {}
        deletes = data.get("deletes") or {}

        def p(rel: str) -> Path:
            return (repo_root / rel).resolve()

        return Settings(
            repo_root=repo_root,
            server_host=str(server.get("host") or "127.0.0.1"),
            server_port=int(server.get("port") or 8765),
            data_dir=p(str(paths.get("data_dir") or "data")),
            logs_dir=p(str(paths.get("logs_dir") or "data/logs")),
            thumbnail_cache_dir=p(str(paths.get("thumbnail_cache_dir") or "data/thumbnail_cache")),
            scan_artifacts_dir=p(str(paths.get("scan_artifacts_dir") or "data/scans")),
            mount_point=p(str(paths.get("mount_point") or "data/iphone_mount")),
            delete_chunk_size=int(deletes.get("chunk_size") or 40),
            ideviceinfo=tools.get("ideviceinfo"),
            idevice_id=tools.get("idevice_id"),
            ifuse=tools.get("ifuse"),
            ui_open_browser=bool(ui.get("open_browser", True)),
            thumbnail_max_edge=int(ui.get("thumbnail_max_edge") or 320),
            thumbnail_jpeg_quality=int(ui.get("thumbnail_jpeg_quality") or 72),
            thumbnail_cache_max_mb=int(ui.get("thumbnail_cache_max_mb") or 512),
            max_concurrent_thumbnails=int(ui.get("max_concurrent_thumbnails") or 3),
            sse_poll_interval_ms=int(ui.get("sse_poll_interval_ms") or 800),
            duplicates_auto_face_eye=bool(auto.get("face_eye", False)),
            duplicates_face_eye_max_images=int(auto.get("face_eye_max_images_per_group") or 6),
            phash_threshold=int(dup.get("phash_threshold") or 6),
            fuzzy_phash_max_hamming=int(dup.get("fuzzy_phash_max_hamming") or 12),
            fuzzy_phash_max_dim=int(dup.get("fuzzy_phash_max_dim") or 128),
            fuzzy_roll_batch_size=int(dup.get("fuzzy_roll_batch_size") or 1000),
            log_level=str(logging.get("level") or "INFO"),
        )
