"""Rotating file logging under config paths (no env-based config)."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("iphone_cleanup")


def setup_file_logging(log_dir: Path, level_name: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"
    handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.handlers.clear()
    _LOG.setLevel(getattr(logging, level_name.upper(), logging.INFO))
    _LOG.addHandler(handler)
    _LOG.propagate = False


def log_event(message: str, **fields: Any) -> None:
    """Append a single-line JSON record for job / pipeline correlation."""
    try:
        payload = {"msg": message, **fields}
        _LOG.info(json.dumps(payload, default=str))
    except Exception:
        _LOG.info(message)


def log_activity(message: str) -> None:
    """Mirror UI activity lines into the same rotating app.log file."""
    if not message or not _LOG.handlers:
        return
    _LOG.info(f"[activity] {message}")
