"""Tests for iphone_cleanup.delete."""

from __future__ import annotations

from pathlib import Path

from iphone_cleanup import delete as delmod


def test_resolve_under_mount_ok(tmp_path: Path):
    root = tmp_path / "mount"
    root.mkdir()
    f = root / "a.jpg"
    f.write_bytes(b"x")
    resolved = delmod.resolve_under_mount(f, root)
    assert resolved == f.resolve()


def test_resolve_under_mount_not_file(tmp_path: Path):
    root = tmp_path / "mount"
    root.mkdir()
    assert delmod.resolve_under_mount(root, root) is None


def test_resolve_under_mount_outside(tmp_path: Path):
    root = tmp_path / "mount"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("n", encoding="utf-8")
    assert delmod.resolve_under_mount(outside, root) is None


def test_resolve_under_mount_dotdot_parts(tmp_path: Path):
    root = tmp_path / "mount"
    root.mkdir()
    # Path with ".." in parts still inside — function rejects any .. in candidate.parts
    cand = root / ".." / "mount" / "x.txt"
    cand.write_text("z", encoding="utf-8")
    assert delmod.resolve_under_mount(cand, root) is None


def test_delete_paths_happy_path(tmp_path: Path):
    root = tmp_path / "m"
    root.mkdir()
    a = root / "d.jpg"
    b = root / "keep.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    res = delmod.delete_paths([str(a)], root)
    assert not a.exists()
    assert b.exists()
    assert str(a.resolve()) in res["deleted"]
    assert res["failed"] == []
    assert res["skipped"] == []


def test_delete_paths_skipped_outside(tmp_path: Path):
    root = tmp_path / "m"
    root.mkdir()
    res = delmod.delete_paths([str(tmp_path / "evil.txt")], root)
    assert res["deleted"] == []
    assert res["skipped"]


def test_delete_paths_chunked_progress(tmp_path: Path):
    root = tmp_path / "m"
    root.mkdir()
    paths = []
    for i in range(7):
        p = root / f"f{i}.txt"
        p.write_text(str(i), encoding="utf-8")
        paths.append(str(p))
    calls: list[tuple[int, int, int, int, int]] = []

    def on_progress(done: int, total: int, dn: int, fn: int, sn: int, _last: str = "") -> None:
        calls.append((done, total, dn, fn, sn))

    res = delmod.delete_paths_chunked(paths, root, chunk_size=3, on_progress=on_progress)
    assert len(res["deleted"]) == 7
    assert calls and calls[-1][0] == 7
