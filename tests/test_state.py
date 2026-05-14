"""Tests for iphone_cleanup.state."""

from __future__ import annotations

from iphone_cleanup.state import AppState, JobStatus, Phase


def test_job_status_to_dict():
    j = JobStatus(job_id="abc", kind="scan", label="L", running=True, message="m")
    d = j.to_dict()
    assert d["job_id"] == "abc"
    assert d["running"] is True
    assert d["progress_current"] is None
    assert d["progress_total"] is None


def test_app_state_snapshot_and_jobs():
    s = AppState()
    s.set_phase(Phase.mounted)
    s.device_info = {"trusted": True}
    snap = s.snapshot()
    assert snap["phase"] == "mounted"
    assert snap["device"]["trusted"] is True
    assert snap["activity_log"] == []

    job = s.start_job("x", "label")
    log_lines = list(s.activity_log)
    assert len(log_lines) >= 3
    assert any("JOB START" in ln for ln in log_lines)
    assert any("label" in ln for ln in log_lines)
    assert job.running
    s.update_job(job.job_id, "half", progress_current=3, progress_total=10)
    assert any("half" in ln for ln in s.activity_log)
    s.finish_job(job.job_id, "done")
    snap2 = s.snapshot()
    jobs = {j["job_id"]: j for j in snap2["jobs"]}
    assert jobs[job.job_id]["running"] is False
    assert jobs[job.job_id]["message"] == "done"
    assert jobs[job.job_id]["progress_current"] is None
    assert jobs[job.job_id]["progress_total"] is None


def test_clear_activity_log():
    s = AppState()
    s.append_activity("keep me")
    assert len(s.activity_log) == 1
    s.clear_activity_log()
    assert len(s.activity_log) == 0


def test_finish_job_logs_even_without_summary_message():
    s = AppState()
    job = s.start_job("z", "L")
    n_before = len(s.activity_log)
    s.finish_job(job.job_id, "")
    assert len(s.activity_log) > n_before
    assert any("FINISHED" in ln and "no summary" in ln for ln in s.activity_log)


def test_next_event_seq_monotonic():
    s = AppState()
    a = s.next_event_seq()
    b = s.next_event_seq()
    assert b == a + 1


def test_snapshot_fuzzy_roll_fields():
    s = AppState()
    s.fuzzy_roll_next_start = 2000
    s.fuzzy_roll_total = 10000
    snap = s.snapshot()
    assert snap["fuzzy_roll_next_start"] == 2000
    assert snap["fuzzy_roll_total"] == 10000
    assert snap["fuzzy_roll_exhausted"] is False
    s.fuzzy_roll_next_start = 10000
    assert s.snapshot()["fuzzy_roll_exhausted"] is True


def test_set_phase_with_message_sets_last_error():
    s = AppState()
    s.set_phase(Phase.idle, message="oops")
    assert s.last_error == "oops"
