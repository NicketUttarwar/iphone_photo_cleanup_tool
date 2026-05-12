"""Tests for iphone_cleanup.app_context."""

from __future__ import annotations

from iphone_cleanup.state import Phase


def test_effective_keep_mode_runtime_over_yaml(app_ctx):
    app_ctx.settings = app_ctx.settings  # frozen; duplicates_keep_mode from fixture
    assert app_ctx.effective_keep_mode() == "manual"
    app_ctx.state.runtime_keep_mode = "auto_best"
    assert app_ctx.effective_keep_mode() == "auto_best"
    app_ctx.state.runtime_keep_mode = None
    assert app_ctx.effective_keep_mode() == "manual"


def test_effective_keep_mode_empty_string_falls_through(app_ctx):
    app_ctx.state.runtime_keep_mode = ""
    # falsy -> use settings
    assert app_ctx.effective_keep_mode() == "manual"
