"""Tests for iphone_cleanup.prefs."""

from __future__ import annotations

import json

from iphone_cleanup import prefs
from iphone_cleanup.settings import Settings


def test_load_ui_state_missing(settings: Settings):
    assert prefs.load_ui_state(settings) == {}


def test_load_ui_state_invalid_json(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    p = settings.data_dir / "ui_state.json"
    p.write_text("{not json", encoding="utf-8")
    assert prefs.load_ui_state(settings) == {}


def test_load_ui_state_not_dict(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    p = settings.data_dir / "ui_state.json"
    p.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert prefs.load_ui_state(settings) == {}


def test_save_keep_mode_roundtrip(settings: Settings):
    prefs.save_keep_mode(settings, "manual")
    assert prefs.load_ui_state(settings).get("keep_mode") == "manual"
    prefs.save_keep_mode(settings, "auto_best")
    assert prefs.load_ui_state(settings).get("keep_mode") == "auto_best"


def test_save_keep_mode_invalid_ignored(settings: Settings):
    prefs.save_keep_mode(settings, "bogus")
    assert "keep_mode" not in prefs.load_ui_state(settings)
