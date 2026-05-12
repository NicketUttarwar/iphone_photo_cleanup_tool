"""HTTP API tests (FastAPI TestClient) with subprocess boundaries mocked."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from iphone_cleanup import documents
from iphone_cleanup.state import Phase


def test_health(test_client):
    r = test_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_and_prerequisites(test_client):
    assert test_client.get("/").status_code == 200
    assert test_client.get("/prerequisites").status_code == 200


def test_api_status_includes_keep_mode(test_client, app_ctx):
    r = test_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "phase" in body and body["keep_mode"] == app_ctx.effective_keep_mode()
    assert "document_batches" in body and isinstance(body["document_batches"], list)


@patch("iphone_cleanup.api.routes.device_bridge.detect_device")
def test_api_device_sets_phase_when_trusted(mock_det, test_client, app_ctx):
    mock_det.return_value = {"trusted": True, "udid": "u1", "name": "Phone", "ios_version": "17", "error": None}
    app_ctx.state.set_phase(Phase.idle)
    r = test_client.get("/api/device")
    assert r.status_code == 200
    assert r.json()["trusted"] is True
    assert app_ctx.state.phase == Phase.device_detected


@patch("iphone_cleanup.api.routes.mount.mount_media")
@patch("iphone_cleanup.api.routes.device_bridge.detect_device")
def test_api_mount_success(mock_dev, mock_mount, test_client, app_ctx, tmp_path):
    mock_dev.return_value = {"trusted": True, "udid": "ud", "error": None}
    mock_mount.return_value = (True, "Mounted.", None)
    app_ctx.state.device_info = {"trusted": True, "udid": "ud"}
    r = test_client.post("/api/mount")
    assert r.status_code == 200
    assert app_ctx.state.phase == Phase.mounted
    assert app_ctx.state.mount_path is not None


@patch("iphone_cleanup.api.routes.mount.unmount_path")
def test_api_unmount(mock_um, test_client, app_ctx, settings):
    mock_um.return_value = (True, "ok")
    app_ctx.state.set_phase(Phase.mounted)
    app_ctx.state.mount_path = Path("/tmp/fake")
    q = documents.document_quarantine_root(settings.data_dir) / "abatch"
    q.mkdir(parents=True, exist_ok=True)
    (q / "manifest.json").write_text('{"entries": []}', encoding="utf-8")
    settings.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.thumbnail_cache_dir / "x.jpg").write_bytes(b"\xff\xd8\xff")
    r = test_client.post("/api/unmount")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert app_ctx.state.phase == Phase.idle
    assert not q.exists()
    assert not (settings.thumbnail_cache_dir / "x.jpg").exists()


@patch("iphone_cleanup.api.routes.device_bridge.detect_device")
def test_api_mount_rejects_untrusted(mock_det, test_client, app_ctx):
    mock_det.return_value = {"trusted": False, "error": "unlock"}
    app_ctx.state.device_info = None
    r = test_client.post("/api/mount")
    assert r.status_code == 400


def test_api_scan_start_without_mount(test_client, app_ctx):
    app_ctx.state.set_phase(Phase.idle)
    r = test_client.post("/api/scan/start")
    assert r.status_code == 400


def test_api_scan_cancel_not_running(test_client, app_ctx):
    app_ctx.state.set_phase(Phase.mounted)
    r = test_client.post("/api/scan/cancel")
    assert r.status_code == 400


def _write_dup_mount(mount: Path) -> None:
    dcim = mount / "DCIM"
    _write_dup_mount_in(dcim)


def _write_dup_mount_in(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (40, 30), color=(10, 90, 200))
    for n in ("a.jpg", "b.jpg"):
        img.save(dir_path / n, "JPEG", quality=90)


def test_scan_and_groups_flow(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    _write_dup_mount(mount_root)
    app_ctx.state.set_phase(Phase.mounted)
    app_ctx.state.mount_path = mount_root.resolve()
    r = test_client.post("/api/scan/start")
    assert r.status_code == 200
    deadline = time.time() + 60
    while time.time() < deadline:
        app_ctx.state.lock.acquire()
        try:
            ph = app_ctx.state.phase
            gc = len(app_ctx.state.duplicate_groups)
        finally:
            app_ctx.state.lock.release()
        if ph == Phase.reviewing and gc >= 1:
            break
        time.sleep(0.05)
    assert app_ctx.state.phase == Phase.reviewing
    gr = test_client.get("/api/scan/groups")
    assert gr.status_code == 200
    data = gr.json()
    assert data["keep_mode"] == "manual"
    assert len(data["groups"]) >= 1


def test_keep_mode_and_selection(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    _write_dup_mount(mount_root)
    app_ctx.state.set_phase(Phase.reviewing)
    p1 = str((mount_root / "DCIM" / "a.jpg").resolve())
    p2 = str((mount_root / "DCIM" / "b.jpg").resolve())
    gid = "g_test_1"
    app_ctx.state.duplicate_groups = [{"id": gid, "paths": [p1, p2], "recommendedKeep": p1}]
    app_ctx.state.group_keep = {gid: p1}
    r = test_client.post("/api/keep-mode", json={"mode": "manual"})
    assert r.status_code == 200
    sel = test_client.post("/api/selection", json={"group_id": gid, "keep_path": p2})
    assert sel.status_code == 200
    assert app_ctx.state.group_keep[gid] == p2


def test_selection_unknown_group(test_client, app_ctx):
    app_ctx.state.duplicate_groups = []
    r = test_client.post("/api/selection", json={"group_id": "x", "keep_path": "/a"})
    assert r.status_code == 404


def test_selection_bad_keep_path(test_client, app_ctx):
    gid = "g1"
    app_ctx.state.duplicate_groups = [{"id": gid, "paths": ["/only/one.jpg"]}]
    r = test_client.post("/api/selection", json={"group_id": gid, "keep_path": "/other.jpg"})
    assert r.status_code == 400


def test_delete_wrong_confirm(test_client, app_ctx):
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = Path("/tmp")
    r = test_client.post("/api/delete", json={"paths": [], "confirm": "no"})
    assert r.status_code == 400


def test_delete_happy_path(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    sub = mount_root / "d"
    _write_dup_mount_in(sub)
    keep = str((sub / "a.jpg").resolve())
    dup = str((sub / "b.jpg").resolve())
    gid = "gdel1"
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.duplicate_groups = [{"id": gid, "paths": [keep, dup], "recommendedKeep": keep}]
    app_ctx.state.group_keep = {gid: keep}
    r = test_client.post("/api/delete", json={"paths": [], "confirm": "DELETE_SELECTED_FILES"})
    assert r.status_code == 200
    deadline = time.time() + 30
    while time.time() < deadline:
        if not Path(dup).is_file():
            break
        time.sleep(0.05)
    assert not Path(dup).is_file()
    assert Path(keep).is_file()
    assert app_ctx.state.phase == Phase.reviewing


def test_thumbnail_dotdot_rejected(test_client, app_ctx):
    app_ctx.state.mount_path = Path("/tmp")
    r = test_client.get("/api/thumbnail", params={"relpath": ".."})
    assert r.status_code == 400


def test_thumbnail_missing_file(test_client, app_ctx, settings):
    app_ctx.state.mount_path = settings.mount_point
    (settings.mount_point).mkdir(parents=True, exist_ok=True)
    r = test_client.get("/api/thumbnail", params={"relpath": "nope.jpg"})
    assert r.status_code == 404


def test_thumbnail_ok(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    mount_root.mkdir(parents=True, exist_ok=True)
    rel = Path("sub") / "t.jpg"
    full = mount_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), "cyan").save(full, "JPEG")
    app_ctx.state.mount_path = mount_root.resolve()
    r = test_client.get("/api/thumbnail", params={"relpath": str(rel).replace(chr(92), "/")})
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/")


def test_api_documents_preview_requires_mount(test_client, app_ctx):
    r = test_client.get("/api/documents/preview")
    assert r.status_code == 400


def test_api_documents_remove_bad_confirm(test_client, app_ctx, settings):
    settings.mount_point.mkdir(parents=True, exist_ok=True)
    app_ctx.state.mount_path = settings.mount_point.resolve()
    app_ctx.state.set_phase(Phase.mounted)
    r = test_client.post(
        "/api/documents/remove",
        json={"scope": "all", "confirm": "WRONG", "include_visual_fallback": False},
    )
    assert r.status_code == 400


def test_api_documents_remove_undo_roundtrip(test_client, app_ctx, settings):
    mount = settings.mount_point
    mount.mkdir(parents=True, exist_ok=True)
    p = mount / "receipt_roundtrip.jpg"
    Image.new("RGB", (40, 40), "yellow").save(p, "JPEG")
    app_ctx.state.mount_path = mount.resolve()
    app_ctx.state.set_phase(Phase.mounted)
    pr = test_client.get("/api/documents/preview", params={"scope": "all"})
    assert pr.status_code == 200
    assert pr.json()["count"] >= 1
    r = test_client.post(
        "/api/documents/remove",
        json={"scope": "all", "confirm": "REMOVE_TAGGED_DOCUMENTS", "include_visual_fallback": False},
    )
    assert r.status_code == 200
    for _ in range(80):
        time.sleep(0.05)
        st = test_client.get("/api/status").json()
        doc_jobs = [j for j in st.get("jobs", []) if j.get("kind") == "document_remove"]
        if doc_jobs and not doc_jobs[-1].get("running"):
            break
    assert not p.is_file()
    u = test_client.post("/api/documents/undo", json={})
    assert u.status_code == 200
    assert p.is_file()


def test_api_events_route_registered(app_ctx):
    """Do not iterate /api/events under TestClient — it can block indefinitely on some stacks."""
    from iphone_cleanup.main import create_app

    app = create_app(app_ctx)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/events" in paths


def test_openapi_documents_sse_events_route(app_ctx):
    from iphone_cleanup.main import create_app

    app = create_app(app_ctx)
    schema = app.openapi()
    assert "/api/events" in schema["paths"]
    get_op = schema["paths"]["/api/events"]["get"]
    # Response may reference text/event-stream in content types
    assert get_op is not None
