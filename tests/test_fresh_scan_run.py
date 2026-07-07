"""Fresh scan run: purge saved sessions/cache and auto-scan when mounted."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from iphone_cleanup import scan, scan_sessions
from iphone_cleanup.session_bootstrap import prepare_fresh_scan_run, schedule_fresh_scans_if_mounted
from iphone_cleanup.state import Phase


def test_clear_all_sessions(tmp_path: Path):
    root = tmp_path / "user_scans"
    scan_sessions.persist_session(
        root,
        groups=[{"id": "g1", "paths": ["/a"], "scan_kind": "exact"}],
        scan_kind="exact",
        mount_udid=None,
        mount_path=None,
    )
    assert len(scan_sessions.list_sessions(root)) == 1
    removed = scan_sessions.clear_all_sessions(root)
    assert removed == 1
    assert scan_sessions.list_sessions(root) == []
    assert scan_sessions.read_active_ids(root) == {}


def test_clear_fuzzy_roll_cache(tmp_path: Path):
    base = tmp_path / "scans" / "fuzzy_roll"
    base.mkdir(parents=True)
    (base / "dev_abc.json").write_text("{}", encoding="utf-8")
    (base / "dev_def.json").write_text("{}", encoding="utf-8")
    assert scan.clear_fuzzy_roll_cache(tmp_path / "scans") == 2
    assert scan.clear_fuzzy_roll_cache(tmp_path / "scans") == 0


def test_prepare_fresh_scan_run_clears_disk_and_memory(app_ctx):
    root = app_ctx.settings.user_scans_dir
    scan_sessions.persist_session(
        root,
        groups=[{"id": "g1", "paths": ["/a"], "scan_kind": "exact"}],
        scan_kind="exact",
        mount_udid="u1",
        mount_path=None,
    )
    cache_dir = app_ctx.settings.scan_artifacts_dir / "fuzzy_roll"
    cache_dir.mkdir(parents=True)
    (cache_dir / "u1_tag.json").write_text("{}", encoding="utf-8")
    app_ctx.state.duplicate_groups = [{"id": "g1", "paths": ["/a"], "scan_kind": "exact"}]
    app_ctx.state.active_exact_scan_session_id = "old"

    prepare_fresh_scan_run(app_ctx)

    assert scan_sessions.list_sessions(root) == []
    assert not list(cache_dir.glob("*.json"))
    assert app_ctx.state.duplicate_groups == []
    assert app_ctx.state.active_exact_scan_session_id is None


@patch("iphone_cleanup.session_bootstrap.threading.Thread")
def test_schedule_fresh_scans_when_mounted(mock_thread, app_ctx, settings):
    mp = settings.mount_point.resolve()
    mp.mkdir(parents=True, exist_ok=True)
    app_ctx.auto_scan_on_mount = True
    app_ctx.state.mount_path = mp
    app_ctx.state.set_phase(Phase.mounted)

    started = schedule_fresh_scans_if_mounted(app_ctx)

    assert started is True
    assert app_ctx.state.phase == Phase.scanning
    assert app_ctx.state.pending_rescan_kind == "fuzzy"
    mock_thread.assert_called_once()


def test_schedule_skips_when_groups_already_loaded(app_ctx, settings):
    mp = settings.mount_point.resolve()
    mp.mkdir(parents=True, exist_ok=True)
    app_ctx.state.mount_path = mp
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.duplicate_groups = [{"id": "g1", "paths": ["/a"], "scan_kind": "exact"}]

    assert schedule_fresh_scans_if_mounted(app_ctx) is False
    assert app_ctx.state.phase == Phase.reviewing
