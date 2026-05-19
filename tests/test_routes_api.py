"""HTTP API tests (FastAPI TestClient) with subprocess boundaries mocked."""

from __future__ import annotations

import threading
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
    assert "activity_log" in body and isinstance(body["activity_log"], list)


def test_api_activity_log_clear(test_client, app_ctx):
    app_ctx.state.append_activity("operator-visible test line")
    assert len(app_ctx.state.activity_log) >= 1
    r = test_client.post("/api/activity-log/clear")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert len(app_ctx.state.activity_log) == 0


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
    app_ctx.state.set_phase(Phase.device_detected)
    app_ctx.state.scan_cancel_event.set()
    r = test_client.post("/api/mount")
    assert r.status_code == 200
    assert r.json().get("started") is True
    deadline = time.time() + 5.0
    while time.time() < deadline and app_ctx.state.phase != Phase.mounted:
        time.sleep(0.02)
    assert app_ctx.state.phase == Phase.mounted
    assert app_ctx.state.mount_path is not None
    assert not app_ctx.state.scan_cancel_event.is_set()


@patch("iphone_cleanup.api.routes.mount.mount_media")
@patch("iphone_cleanup.api.routes.device_bridge.detect_device")
def test_api_mount_409_while_mount_in_progress(mock_dev, mock_mount, test_client, app_ctx):
    gate = threading.Event()

    def blocked_mount(*_a, **_k):
        if not gate.wait(timeout=5.0):
            return (False, "timed out", None)
        return (True, "Mounted.", None)

    mock_dev.return_value = {"trusted": True, "udid": "ud", "error": None}
    mock_mount.side_effect = blocked_mount
    app_ctx.state.device_info = {"trusted": True, "udid": "ud"}
    app_ctx.state.set_phase(Phase.device_detected)
    r1 = test_client.post("/api/mount")
    assert r1.status_code == 200
    assert app_ctx.state.phase == Phase.mounting
    r2 = test_client.post("/api/mount")
    assert r2.status_code == 409
    gate.set()
    deadline = time.time() + 5.0
    while time.time() < deadline and app_ctx.state.phase != Phase.mounted:
        time.sleep(0.02)
    assert app_ctx.state.phase == Phase.mounted


@patch("iphone_cleanup.api.routes.mount.unmount_path")
def test_api_unmount(mock_um, test_client, app_ctx, settings):
    mock_um.return_value = (True, "ok")
    app_ctx.state.set_phase(Phase.mounted)
    app_ctx.state.mount_path = Path("/tmp/fake")
    app_ctx.state.duplicate_groups = [
        {"id": "gx", "paths": ["/x/a.jpg"], "recommendedKeep": "/x/a.jpg", "recommendedKeeps": ["/x/a.jpg"]}
    ]
    app_ctx.state.group_keep = {"gx": ["/x/a.jpg"]}
    q = documents.document_quarantine_root(settings.data_dir) / "abatch"
    q.mkdir(parents=True, exist_ok=True)
    (q / "manifest.json").write_text('{"entries": []}', encoding="utf-8")
    settings.thumbnail_cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.thumbnail_cache_dir / "x.jpg").write_bytes(b"\xff\xd8\xff")
    r = test_client.post("/api/unmount")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert app_ctx.state.phase == Phase.idle
    assert app_ctx.state.duplicate_groups == []
    assert app_ctx.state.group_keep == {}
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
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("noop") is True


@patch("iphone_cleanup.api.routes.scan.run_fuzzy_roll_scan_batch")
@patch("iphone_cleanup.api.routes.scan.scan_duplicates")
def test_api_scan_start_replaces_while_scan_running(mock_dup, mock_batch, test_client, app_ctx, settings):
    calls = {"n": 0}

    def slow_dup(*_a, cancel_event=None, **_k):
        calls["n"] += 1
        if calls["n"] >= 2:
            return []
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return []
            time.sleep(0.01)
        return []

    def slow_batch(*_a, cancel_event=None, **_k):
        calls["n"] += 1
        if calls["n"] >= 2:
            return ([], 0, 0)
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return ([], 0, 0)
            time.sleep(0.01)
        return ([], 0, 0)

    mock_dup.side_effect = slow_dup
    mock_batch.side_effect = slow_batch
    mp = settings.mount_point
    mp.mkdir(parents=True, exist_ok=True)
    app_ctx.state.set_phase(Phase.mounted)
    app_ctx.state.mount_path = mp.resolve()
    app_ctx.state.mount_udid = "pytest-udid"
    r1 = test_client.post("/api/scan/start")
    assert r1.status_code == 200
    r2 = test_client.post("/api/scan/start?kind=fuzzy")
    assert r2.status_code == 200
    body = r2.json()
    assert body.get("replacing") is True
    assert body.get("scan_kind") == "fuzzy"
    deadline = time.time() + 10.0
    while time.time() < deadline and app_ctx.state.phase == Phase.scanning:
        time.sleep(0.05)
    assert app_ctx.state.phase != Phase.scanning
    assert calls["n"] >= 2


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
    assert data["keep_mode"] == "auto_best"
    assert len(data["groups"]) >= 1


def test_selection_after_groups_loaded(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    _write_dup_mount(mount_root)
    app_ctx.state.set_phase(Phase.reviewing)
    p1 = str((mount_root / "DCIM" / "a.jpg").resolve())
    p2 = str((mount_root / "DCIM" / "b.jpg").resolve())
    gid = "g_test_1"
    app_ctx.state.duplicate_groups = [{"id": gid, "paths": [p1, p2], "recommendedKeep": p1, "recommendedKeeps": [p1]}]
    app_ctx.state.group_keep = {gid: [p1]}
    sel = test_client.post("/api/selection", json={"group_id": gid, "keep_path": p2})
    assert sel.status_code == 200
    assert app_ctx.state.group_keep[gid] == [p2]


def test_selection_unknown_group(test_client, app_ctx):
    app_ctx.state.duplicate_groups = []
    r = test_client.post("/api/selection", json={"group_id": "x", "keep_path": "/a"})
    assert r.status_code == 404


def test_selection_bad_keep_path(test_client, app_ctx):
    gid = "g1"
    app_ctx.state.duplicate_groups = [{"id": gid, "paths": ["/only/one.jpg"]}]
    r = test_client.post("/api/selection", json={"group_id": gid, "keep_path": "/other.jpg"})
    assert r.status_code == 400


def test_selection_toggle_and_keep_paths(test_client, app_ctx):
    gid = "g1"
    p1 = "/mnt/a.jpg"
    p2 = "/mnt/b.jpg"
    app_ctx.state.duplicate_groups = [
        {"id": gid, "paths": [p1, p2], "recommendedKeep": p1, "recommendedKeeps": [p1]}
    ]
    app_ctx.state.group_keep = {gid: [p1]}
    assert test_client.post("/api/selection", json={"group_id": gid, "toggle_path": p2}).status_code == 200
    assert set(app_ctx.state.group_keep[gid]) == {p1, p2}
    r = test_client.post("/api/selection", json={"group_id": gid, "keep_paths": [p2]})
    assert r.status_code == 200
    assert app_ctx.state.group_keep[gid] == [p2]


def test_selection_two_actions_rejected(test_client, app_ctx):
    gid = "g1"
    app_ctx.state.duplicate_groups = [
        {"id": gid, "paths": ["/a.jpg", "/b.jpg"], "recommendedKeep": "/a.jpg", "recommendedKeeps": ["/a.jpg"]}
    ]
    r = test_client.post(
        "/api/selection",
        json={"group_id": gid, "keep_path": "/a.jpg", "toggle_path": "/b.jpg"},
    )
    assert r.status_code == 400


def test_scan_groups_and_delete_scoped_by_size_filter(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    app_ctx.state.set_phase(Phase.reviewing)
    mount_root.mkdir(parents=True, exist_ok=True)
    app_ctx.state.mount_path = mount_root.resolve()
    _write_dup_mount_in(mount_root)
    p2a = str((mount_root / "a.jpg").resolve())
    p2b = str((mount_root / "b.jpg").resolve())
    p3a = str((mount_root / "tri_a.jpg").resolve())
    p3b = str((mount_root / "tri_b.jpg").resolve())
    p3c = str((mount_root / "tri_c.jpg").resolve())
    for p in (p3a, p3b, p3c):
        Path(p).write_bytes(b"x")
    g2 = "g_two"
    g3 = "g_three"
    app_ctx.state.duplicate_groups = [
        {
            "id": g2,
            "paths": [p2a, p2b],
            "scan_kind": "exact",
            "recommendedKeep": p2a,
            "recommendedKeeps": [p2a],
        },
        {
            "id": g3,
            "paths": [p3a, p3b, p3c],
            "scan_kind": "exact",
            "recommendedKeep": p3a,
            "recommendedKeeps": [p3a],
        },
    ]
    app_ctx.state.group_keep = {g2: [p2a], g3: [p3a]}
    gr = test_client.get("/api/scan/groups", params={"kind": "exact", "size_filter": "2"})
    assert gr.status_code == 200
    data = gr.json()
    assert data["total"] == 1
    assert len(data["groups"]) == 1
    assert data["size_counts"]["2"] == 1
    assert data["size_counts"]["3"] == 1
    pr = test_client.get("/api/delete/preview", params={"kind": "exact", "size_filter": "2"})
    assert pr.status_code == 200
    assert pr.json()["file_count"] == 1
    pr3 = test_client.get("/api/delete/preview", params={"kind": "exact", "size_filter": "3"})
    assert pr3.json()["file_count"] == 2


def test_delete_preview_all_kept_zero_files(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    sub = mount_root / "d2"
    _write_dup_mount_in(sub)
    p1 = str((sub / "a.jpg").resolve())
    p2 = str((sub / "b.jpg").resolve())
    gid = "gallkeep"
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.duplicate_groups = [
        {"id": gid, "paths": [p1, p2], "recommendedKeep": p1, "recommendedKeeps": [p1, p2]}
    ]
    app_ctx.state.group_keep = {gid: [p1, p2]}
    r = test_client.get("/api/delete/preview")
    assert r.status_code == 200
    assert r.json()["file_count"] == 0
    assert r.json()["groups_with_deletions"] == 0


def test_api_scan_fuzzy_finishes(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    burst = mount_root / "DCIM" / "burst"
    burst.mkdir(parents=True)
    img = Image.new("RGB", (40, 30), color=(200, 100, 50))
    for n in ("img1.jpg", "img2.jpg"):
        img.save(burst / n, "JPEG", quality=92)
    app_ctx.state.set_phase(Phase.mounted)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.mount_udid = "pytest-udid"
    r = test_client.post("/api/scan/start?kind=fuzzy")
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
    assert any(str(g.get("scan_kind")) == "fuzzy" for g in app_ctx.state.duplicate_groups)
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
    app_ctx.state.duplicate_groups = [
        {"id": gid, "paths": [keep, dup], "recommendedKeep": keep, "recommendedKeeps": [keep]}
    ]
    app_ctx.state.group_keep = {gid: [keep]}
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


def test_thumbnail_without_live_mount_uses_configured_mount_point(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    mount_root.mkdir(parents=True, exist_ok=True)
    rel = Path("DCIM") / "offline.jpg"
    full = mount_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 18), "yellow").save(full, "JPEG")
    app_ctx.state.mount_path = None
    r = test_client.get("/api/thumbnail", params={"relpath": str(rel).replace(chr(92), "/")})
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/")


def test_scan_groups_include_relpaths(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    mount_root.mkdir(parents=True, exist_ok=True)
    rel = Path("DCIM") / "x.jpg"
    full = mount_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 10), "blue").save(full, "JPEG")
    abs_path = str(full.resolve())
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.duplicate_groups = [
        {
            "id": "g_test",
            "paths": [abs_path],
            "scan_kind": "exact",
            "bytesSavedIfOneKept": 0,
        }
    ]
    r = test_client.get("/api/scan/groups", params={"kind": "exact"})
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert len(groups) == 1
    assert groups[0]["relpaths"] == [str(rel).replace(chr(92), "/")]


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


def test_thumbnail_ok_with_custom_max_edge(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    mount_root.mkdir(parents=True, exist_ok=True)
    rel = Path("sub") / "big.jpg"
    full = mount_root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), "magenta").save(full, "JPEG")
    app_ctx.state.mount_path = mount_root.resolve()
    r = test_client.get(
        "/api/thumbnail",
        params={"relpath": str(rel).replace(chr(92), "/"), "max_edge": 128},
    )
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


def test_delete_preview_counts_and_samples(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    sub = mount_root / "d"
    _write_dup_mount_in(sub)
    keep = str((sub / "a.jpg").resolve())
    dup = str((sub / "b.jpg").resolve())
    gid = "gprev1"
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.duplicate_groups = [
        {"id": gid, "paths": [keep, dup], "recommendedKeep": keep, "recommendedKeeps": [keep]}
    ]
    app_ctx.state.group_keep = {gid: [keep]}
    r = test_client.get("/api/delete/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["file_count"] == 1
    assert body["total_bytes"] > 0
    assert body["groups_with_deletions"] == 1
    assert len(body["thumbnail_samples"]) == 1
    assert "relpath" in body["thumbnail_samples"][0]


def test_api_documents_preview_includes_bytes_and_thumbs(test_client, app_ctx, settings):
    mount = settings.mount_point
    mount.mkdir(parents=True, exist_ok=True)
    p = mount / "receipt_api.jpg"
    Image.new("RGB", (40, 40), "yellow").save(p, "JPEG")
    app_ctx.state.mount_path = mount.resolve()
    app_ctx.state.set_phase(Phase.mounted)
    r = test_client.get("/api/documents/preview", params={"scope": "all"})
    assert r.status_code == 200
    j = r.json()
    assert j["count"] >= 1
    assert j["total_bytes"] > 0
    assert isinstance(j.get("thumbnail_sample_relpaths"), list)


def test_api_finalize_preview_and_holding_thumbnail(test_client, app_ctx, settings):
    mount = settings.mount_point
    mount.mkdir(parents=True, exist_ok=True)
    p = mount / "receipt_fin.jpg"
    Image.new("RGB", (40, 40), "orange").save(p, "JPEG")
    app_ctx.state.mount_path = mount.resolve()
    app_ctx.state.set_phase(Phase.mounted)
    test_client.post(
        "/api/documents/remove",
        json={"scope": "all", "confirm": "REMOVE_TAGGED_DOCUMENTS", "include_visual_fallback": False},
    )
    for _ in range(80):
        time.sleep(0.05)
        st = test_client.get("/api/status").json()
        doc_jobs = [j for j in st.get("jobs", []) if j.get("kind") == "document_remove"]
        if doc_jobs and not doc_jobs[-1].get("running"):
            break
    pr = test_client.get("/api/documents/finalize-preview", params={"all_batches": True})
    assert pr.status_code == 200
    pj = pr.json()
    assert pj["file_count"] >= 1
    assert pj["total_bytes"] > 0
    assert pj.get("samples")
    s0 = pj["samples"][0]
    th = test_client.get(
        "/api/documents/holding-thumbnail",
        params={"batch_id": s0["batch_id"], "stored": s0["stored"]},
    )
    assert th.status_code == 200
    assert th.headers.get("content-type", "").startswith("image/")


def test_openapi_documents_sse_events_route(app_ctx):
    from iphone_cleanup.main import create_app

    app = create_app(app_ctx)
    schema = app.openapi()
    assert "/api/events" in schema["paths"]
    get_op = schema["paths"]["/api/events"]["get"]
    # Response may reference text/event-stream in content types
    assert get_op is not None
