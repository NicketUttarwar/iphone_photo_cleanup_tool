"""Shared fixtures: isolated repo root under tmp_path (pytest tears down automatically)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from iphone_cleanup.app_context import AppCtx
from iphone_cleanup.main import create_app
from iphone_cleanup.settings import Settings, load_merged_settings
from iphone_cleanup.state import AppState


def write_defaults_yaml(repo_root: Path) -> Path:
    cfg = repo_root / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "server": {"host": "127.0.0.1", "port": 18765},
        "paths": {
            "data_dir": "_pytest_data/data",
            "logs_dir": "_pytest_data/logs",
            "thumbnail_cache_dir": "_pytest_data/thumbs",
            "scan_artifacts_dir": "_pytest_data/scans",
            "mount_point": "_pytest_data/mount",
        },
        "deletes": {"chunk_size": 5},
        "tools": {"ideviceinfo": None, "idevice_id": None, "ifuse": None},
        "ui": {
            "open_browser": False,
            "thumbnail_max_edge": 64,
            "thumbnail_jpeg_quality": 60,
            "thumbnail_cache_max_mb": 1,
            "max_concurrent_thumbnails": 2,
            "sse_poll_interval_ms": 200,
        },
        "duplicates": {
            "keep_mode": "manual",
            "auto_best": {"face_eye": False, "face_eye_max_images_per_group": 2},
            "phash_threshold": 6,
            "fuzzy_phash_max_hamming": 12,
            "fuzzy_phash_max_dim": 128,
        },
        "logging": {"level": "INFO"},
    }
    path = cfg / "app.defaults.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    write_defaults_yaml(tmp_path)
    return tmp_path


@pytest.fixture
def defaults_path(repo_root: Path) -> Path:
    return repo_root / "config" / "app.defaults.yaml"


@pytest.fixture
def settings(repo_root: Path, defaults_path: Path) -> Settings:
    merged = load_merged_settings(defaults_path, None)
    return Settings.from_dict(repo_root, merged)


@pytest.fixture
def app_ctx(settings: Settings) -> AppCtx:
    sem = threading.BoundedSemaphore(max(1, settings.max_concurrent_thumbnails))
    return AppCtx(settings=settings, state=AppState(), no_open_browser=True, thumb_semaphore=sem)


@pytest.fixture
def test_client(app_ctx: AppCtx):
    from fastapi.testclient import TestClient

    app = create_app(app_ctx)
    with TestClient(app) as client:
        yield client
