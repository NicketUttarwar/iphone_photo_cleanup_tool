"""Keep-mode API with auto_best (uses auto_best.pick_recommended)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from iphone_cleanup.state import Phase


def _write_dup_mount(mount: Path) -> None:
    dcim = mount / "DCIM"
    dcim.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (40, 30), color=(10, 90, 200))
    for n in ("a.jpg", "b.jpg"):
        img.save(dcim / n, "JPEG", quality=90)


def test_keep_mode_auto_best_updates_recommendations(test_client, app_ctx, settings):
    mount_root = settings.mount_point
    _write_dup_mount(mount_root)
    p1 = str((mount_root / "DCIM" / "a.jpg").resolve())
    p2 = str((mount_root / "DCIM" / "b.jpg").resolve())
    gid = "g_auto"
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.duplicate_groups = [{"id": gid, "paths": [p1, p2], "recommendedKeep": p1}]
    app_ctx.state.group_keep = {gid: p1}
    r = test_client.post("/api/keep-mode", json={"mode": "auto_best"})
    assert r.status_code == 200
    assert app_ctx.effective_keep_mode() == "auto_best"
    rec = next(g["recommendedKeep"] for g in app_ctx.state.duplicate_groups if g["id"] == gid)
    assert rec in (p1, p2)
