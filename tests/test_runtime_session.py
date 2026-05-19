"""runtime_session: disk persistence across refresh / flaky mount probes."""

from __future__ import annotations

import json
from unittest.mock import patch

from iphone_cleanup.runtime_session import load_runtime, persist_runtime, runtime_path
from iphone_cleanup.session_bootstrap import sync_mount_from_disk
from iphone_cleanup.state import Phase


def test_persist_and_load_roundtrip(app_ctx):
    app_ctx.state.set_phase(Phase.reviewing)
    app_ctx.state.mount_path = app_ctx.settings.mount_point
    app_ctx.state.append_activity("persist test line")
    app_ctx.state.fuzzy_roll_next_start = 42
    persist_runtime(app_ctx, force=True)
    path = runtime_path(app_ctx.settings)
    assert path.is_file()

    app_ctx.state.set_phase(Phase.idle)
    app_ctx.state.mount_path = None
    app_ctx.state.activity_log.clear()
    assert load_runtime(app_ctx) is True
    assert app_ctx.state.phase == Phase.reviewing
    assert app_ctx.state.fuzzy_roll_next_start == 42
    assert any("persist test" in line for line in app_ctx.state.activity_log)


def test_load_marks_running_jobs_interrupted(app_ctx):
    from iphone_cleanup.state import JobStatus

    app_ctx.state.jobs["j1"] = JobStatus(
        job_id="j1",
        kind="scan",
        label="Scanning…",
        running=True,
        message="Halfway",
    )
    persist_runtime(app_ctx, force=True)
    app_ctx.state.jobs.clear()
    load_runtime(app_ctx)
    job = app_ctx.state.jobs["j1"]
    assert job.running is False
    assert "interrupted" in job.message.lower()


@patch("iphone_cleanup.session_bootstrap.mount.is_mountpoint", return_value=False)
def test_sync_mount_preserves_scanning_and_mount_path(mock_mp, app_ctx, settings):
    mp = settings.mount_point.resolve()
    mp.mkdir(parents=True, exist_ok=True)
    app_ctx.state.set_phase(Phase.scanning)
    app_ctx.state.mount_path = mp
    app_ctx.state.scan_running_kind = "fuzzy"
    sync_mount_from_disk(app_ctx)
    assert app_ctx.state.phase == Phase.scanning
    assert app_ctx.state.mount_path == mp
    assert app_ctx.state.scan_running_kind == "fuzzy"


def test_runtime_file_version_guard(app_ctx, settings):
    path = runtime_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert load_runtime(app_ctx) is False
