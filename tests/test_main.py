"""Tests for iphone_cleanup.main (app factory)."""

from __future__ import annotations

from iphone_cleanup.main import create_app


def test_create_app_lifespan_health_ok(app_ctx):
    app = create_app(app_ctx)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200


def test_create_app_static_mounted(app_ctx):
    app = create_app(app_ctx)
    r = list(app.routes)
    paths = {getattr(route, "path", None) for route in r}
    assert any(p and p.startswith("/static") for p in paths)
