"""Tests for iphone_cleanup.state."""

from __future__ import annotations

from iphone_cleanup.state import AppState, JobStatus, Phase


def test_job_status_to_dict():
    j = JobStatus(job_id="abc", kind="scan", label="L", running=True, message="m")
    d = j.to_dict()
    assert d["job_id"] == "abc"
    assert d["running"] is True


def test_app_state_snapshot_and_jobs():
    s = AppState()
    s.set_phase(Phase.mounted)
    s.device_info = {"trusted": True}
    snap = s.snapshot()
    assert snap["phase"] == "mounted"
    assert snap["device"]["trusted"] is True

    job = s.start_job("x", "label")
    assert job.running
    s.update_job(job.job_id, "half")
    s.finish_job(job.job_id, "done")
    snap2 = s.snapshot()
    jobs = {j["job_id"]: j for j in snap2["jobs"]}
    assert jobs[job.job_id]["running"] is False
    assert jobs[job.job_id]["message"] == "done"


def test_next_event_seq_monotonic():
    s = AppState()
    a = s.next_event_seq()
    b = s.next_event_seq()
    assert b == a + 1


def test_set_phase_with_message_sets_last_error():
    s = AppState()
    s.set_phase(Phase.idle, message="oops")
    assert s.last_error == "oops"
