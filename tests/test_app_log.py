"""Tests for iphone_cleanup.app_log."""

from __future__ import annotations

from pathlib import Path

from iphone_cleanup.app_log import log_event, setup_file_logging


def test_setup_file_logging_writes(tmp_path: Path):
    log_dir = tmp_path / "logs"
    setup_file_logging(log_dir, "INFO")
    log_event("test_event", foo=1, bar="z")
    log_file = log_dir / "app.log"
    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "test_event" in text
