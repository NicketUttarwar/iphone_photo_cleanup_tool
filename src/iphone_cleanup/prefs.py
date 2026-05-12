"""Persist lightweight UI preferences under data_dir (no environment variables)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iphone_cleanup.settings import Settings

_STATE_NAME = "ui_state.json"


def _path(settings: Settings) -> Path:
    return settings.data_dir / _STATE_NAME


def load_ui_state(settings: Settings) -> dict[str, Any]:
    path = _path(settings)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_keep_mode(settings: Settings, mode: str) -> None:
    if mode not in ("manual", "auto_best"):
        return
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = _path(settings)
    prev = load_ui_state(settings)
    prev["keep_mode"] = mode
    path.write_text(json.dumps(prev, indent=2), encoding="utf-8")
