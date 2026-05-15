"""Tests for iphone_cleanup.app_context."""

from __future__ import annotations


def test_effective_keep_mode_reports_auto_best(app_ctx):
    assert app_ctx.effective_keep_mode() == "auto_best"
