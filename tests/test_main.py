"""Tests for iphone_cleanup.main (app factory)."""

from __future__ import annotations

from iphone_cleanup.main import create_app
from iphone_cleanup.prefs import save_keep_mode


def test_create_app_lifespan_restores_keep_mode_from_prefs(app_ctx):
    save_keep_mode(app_ctx.settings, "auto_best")
    app = create_app(app_ctx)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.get("/health")
    assert app_ctx.state.runtime_keep_mode == "auto_best"


def test_create_app_static_mounted(app_ctx):
    app = create_app(app_ctx)
    r = list(app.routes)
    paths = {getattr(route, "path", None) for route in r}
    assert any(p and p.startswith("/static") for p in paths)
