"""Tests for user_scans session persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from iphone_cleanup import scan, scan_sessions
from iphone_cleanup.state import Phase


def test_persist_and_list_sessions(tmp_path: Path):
    root = tmp_path / "user_scans"
    groups = [{"id": "g1", "paths": ["/a", "/b"], "scan_kind": "exact"}]
    m = scan_sessions.persist_session(
        root,
        groups=groups,
        scan_kind="exact",
        mount_udid="ud1",
        mount_path=tmp_path / "mount",
    )
    assert m["id"]
    assert m["group_count"] == 1
    listed = scan_sessions.list_sessions(root)
    assert len(listed) == 1
    assert listed[0]["id"] == m["id"]
    assert scan_sessions.read_active_id(root) == m["id"]


def test_default_active_prefers_newest_per_kind(tmp_path: Path):
    root = tmp_path / "user_scans"
    m1 = scan_sessions.persist_session(root, groups=[], scan_kind="exact", mount_udid=None, mount_path=None)
    m2 = scan_sessions.persist_session(root, groups=[], scan_kind="fuzzy", mount_udid=None, mount_path=None)
    assert scan_sessions.default_active_id(root, "exact") == m1["id"]
    assert scan_sessions.default_active_id(root, "fuzzy") == m2["id"]


def test_load_session_groups_roundtrip(tmp_path: Path):
    root = tmp_path / "user_scans"
    groups = [{"id": "g1", "paths": ["/x"], "scan_kind": "fuzzy"}]
    m = scan_sessions.persist_session(
        root, groups=groups, scan_kind="fuzzy", mount_udid=None, mount_path=None
    )
    manifest, loaded, sk, gk = scan_sessions.load_session_groups(root, m["id"])
    assert manifest["id"] == m["id"]
    assert loaded == groups
    assert sk == "fuzzy"
    assert gk == {}


def test_sync_active_session_persists_group_keep(app_ctx):
    root = app_ctx.settings.user_scans_dir
    groups = [{"id": "g1", "paths": ["/p1", "/p2"], "scan_kind": "exact"}]
    m = scan_sessions.persist_session(
        root, groups=groups, scan_kind="exact", mount_udid=None, mount_path=None
    )
    app_ctx.state.set_phase(Phase.reviewing)
    scan_sessions.apply_session_to_state(app_ctx, m["id"])
    app_ctx.state.group_keep["g1"] = ["/p2"]
    scan_sessions.sync_active_session_from_state(app_ctx, "exact")
    _, _, _, gk = scan_sessions.load_session_groups(root, m["id"])
    assert gk.get("g1") == ["/p2"]


def test_apply_session_to_state(app_ctx):
    root = app_ctx.settings.user_scans_dir
    groups = [{"id": "g1", "paths": ["/p1", "/p2"], "scan_kind": "exact"}]
    m = scan_sessions.persist_session(
        root, groups=groups, scan_kind="exact", mount_udid="u", mount_path=None
    )
    app_ctx.state.set_phase(Phase.mounted)
    manifest = scan_sessions.apply_session_to_state(app_ctx, m["id"])
    assert manifest["id"] == m["id"]
    assert len(app_ctx.state.duplicate_groups) == 1
    assert app_ctx.state.active_scan_session_id == m["id"]
    assert app_ctx.state.phase == Phase.reviewing
    assert app_ctx.state.group_keep.get("g1")


def test_api_scan_sessions_list_and_activate(test_client, app_ctx):
    root = app_ctx.settings.user_scans_dir
    groups = [{"id": "g1", "paths": ["/a", "/b"], "scan_kind": "exact"}]
    m = scan_sessions.persist_session(
        root, groups=groups, scan_kind="exact", mount_udid=None, mount_path=None
    )
    r = test_client.get("/api/scan/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body["active_session_id"] == m["id"]
    assert len(body["sessions"]) == 1

    app_ctx.state.duplicate_groups = []
    app_ctx.state.group_keep = {}
    app_ctx.state.set_phase(Phase.mounted)
    r2 = test_client.post("/api/scan/sessions/activate", json={"session_id": m["id"]})
    assert r2.status_code == 200
    assert len(app_ctx.state.duplicate_groups) == 1
    assert r2.json()["group_count"] == 1


def test_apply_session_merges_by_kind(app_ctx):
    root = app_ctx.settings.user_scans_dir
    exact = [{"id": "e1", "paths": ["/a", "/b"], "scan_kind": "exact"}]
    fuzzy = [{"id": "f1", "paths": ["/c", "/d"], "scan_kind": "fuzzy"}]
    m_exact = scan_sessions.persist_session(
        root, groups=exact, scan_kind="exact", mount_udid=None, mount_path=None
    )
    m_fuzzy = scan_sessions.persist_session(
        root, groups=fuzzy, scan_kind="fuzzy", mount_udid=None, mount_path=None
    )
    app_ctx.state.duplicate_groups = []
    scan_sessions.apply_session_to_state(app_ctx, m_exact["id"])
    assert len(app_ctx.state.duplicate_groups) == 1
    scan_sessions.apply_session_to_state(app_ctx, m_fuzzy["id"])
    assert len(app_ctx.state.duplicate_groups) == 2
    kinds = {scan_sessions.group_scan_kind(g) for g in app_ctx.state.duplicate_groups}
    assert kinds == {"exact", "fuzzy"}


def test_api_scan_sessions_activate_404(test_client):
    r = test_client.post("/api/scan/sessions/activate", json={"session_id": "no_such_session"})
    assert r.status_code == 404
