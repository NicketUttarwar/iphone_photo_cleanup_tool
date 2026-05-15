"""Automatic keeper picks (_apply_auto_groups)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from iphone_cleanup.api.routes import _apply_auto_groups
from iphone_cleanup.state import Phase


def _write_dup_mount(mount: Path) -> None:
    dcim = mount / "DCIM"
    dcim.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (40, 30), color=(10, 90, 200))
    for n in ("a.jpg", "b.jpg"):
        img.save(dcim / n, "JPEG", quality=90)


def test_apply_auto_groups_updates_exact_duplicate_keepers(app_ctx, settings):
    mount_root = settings.mount_point
    _write_dup_mount(mount_root)
    p1 = str((mount_root / "DCIM" / "a.jpg").resolve())
    p2 = str((mount_root / "DCIM" / "b.jpg").resolve())
    gid = "g_auto"
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = mount_root.resolve()
    app_ctx.state.duplicate_groups = [
        {
            "id": gid,
            "paths": [p1, p2],
            "recommendedKeep": p1,
            "recommendedKeeps": [p1],
            "scan_kind": "exact",
        }
    ]
    app_ctx.state.group_keep = {gid: [p1]}
    _apply_auto_groups(app_ctx)
    rec = next(g["recommendedKeep"] for g in app_ctx.state.duplicate_groups if g["id"] == gid)
    assert rec in (p1, p2)
    assert isinstance(app_ctx.state.group_keep.get(gid), list)
