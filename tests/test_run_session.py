"""run_session: tied to scripts/run.sh lifecycle."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from iphone_cleanup.run_session import begin_run_session, is_run_session_active, session_file
from iphone_cleanup.state import Phase


def test_begin_run_session_writes_file(app_ctx, settings, monkeypatch):
    monkeypatch.setenv("IPHONE_CLEANUP_RUN_ID", "test_run_abc")
    rid = begin_run_session(app_ctx)
    assert rid == "test_run_abc"
    assert app_ctx.run_session_id == "test_run_abc"
    path = session_file(settings)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "test_run_abc"


def test_runtime_not_loaded_when_run_id_mismatches(app_ctx, settings):
    from iphone_cleanup.runtime_session import load_runtime

    app_ctx.run_session_id = "run_new"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "runtime_session.json").write_text(
        json.dumps({"version": 1, "run_session_id": "run_old", "phase": "reviewing"}),
        encoding="utf-8",
    )
    assert load_runtime(app_ctx) is False


def test_is_run_session_active_with_live_shell_pid(app_ctx, settings, monkeypatch):
    monkeypatch.setenv("IPHONE_CLEANUP_RUN_ID", "run_live")
    begin_run_session(app_ctx)
    data = json.loads(session_file(settings).read_text(encoding="utf-8"))
    data["shell_pid"] = os.getpid()
    session_file(settings).write_text(json.dumps(data), encoding="utf-8")
    assert is_run_session_active(app_ctx) is True


def test_set_phase_logs_to_activity(app_ctx):
    app_ctx.state.set_phase(Phase.idle)
    app_ctx.state.activity_log.clear()
    app_ctx.state.activity_log_seq = 0
    app_ctx.state.set_phase(Phase.device_detected)
    lines = list(app_ctx.state.activity_log)
    assert any("PHASE → device_detected" in ln for ln in lines)
