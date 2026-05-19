"""In-memory session state and background job bookkeeping."""

from __future__ import annotations

import enum
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iphone_cleanup.app_log import log_activity

_MISSING = object()


class Phase(str, enum.Enum):
    idle = "idle"
    device_detected = "device_detected"
    mounting = "mounting"
    mounted = "mounted"
    scanning = "scanning"
    reviewing = "reviewing"
    deleting = "deleting"
    unmounting = "unmounting"


# Operator-facing text for the activity log and UI (one line per phase transition).
PHASE_USER_LABELS: dict[str, str] = {
    Phase.idle.value: "Step 1 — Connect iPhone via USB and tap Trust if asked",
    Phase.device_detected.value: "Step 1 done — iPhone trusted on USB",
    Phase.mounting.value: "Step 2 — Mounting iPhone media on this Mac",
    Phase.mounted.value: "Step 2 done — Media mounted; scans and cleanup available",
    Phase.scanning.value: "Step 3 — Duplicate scan running",
    Phase.reviewing.value: "Step 3–4 — Review duplicate groups and choose keepers",
    Phase.deleting.value: "Step 4 — Deleting unmarked duplicates from the phone",
    Phase.unmounting.value: "Step 6 — Unmounting (safe to unplug after this)",
}


@dataclass
class JobStatus:
    job_id: str
    kind: str
    label: str
    running: bool = False
    message: str = ""
    progress_current: int | None = None
    progress_total: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "label": self.label,
            "running": self.running,
            "message": self.message,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class AppState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    phase: Phase = Phase.idle
    last_error: str = ""
    device_info: dict[str, Any] | None = None
    mount_udid: str | None = None
    mount_path: Path | None = None
    ifuse_proc: Any = None
    scan_artifact_path: Path | None = None
    active_scan_session_id: str | None = None
    active_exact_scan_session_id: str | None = None
    active_fuzzy_scan_session_id: str | None = None
    scan_running_kind: str | None = None
    duplicate_groups: list[dict[str, Any]] = field(default_factory=list)
    group_keep: dict[str, list[str]] = field(default_factory=dict)
    jobs: dict[str, JobStatus] = field(default_factory=dict)
    event_seq: int = 0
    scan_cancel_event: threading.Event = field(default_factory=threading.Event)
    scan_cancel_requested: bool = False
    # Fuzzy roll: next slice start index into the cached sorted library; total filled after first batch load.
    fuzzy_roll_next_start: int = 0
    fuzzy_roll_total: int | None = None
    library_indexed_count: int | None = None
    pending_rescan_kind: str | None = None
    last_delete_ledger: dict[str, Any] | None = None
    document_last_ledger: dict[str, Any] | None = None
    # Rolling verbose trace for the UI (timestamps + monotonic line numbers).
    activity_log: deque[str] = field(default_factory=lambda: deque(maxlen=2048))
    activity_log_seq: int = 0

    def next_event_seq(self) -> int:
        with self.lock:
            self.event_seq += 1
            return self.event_seq

    def set_phase(self, phase: Phase, message: str = "", *, detail: str = "") -> None:
        with self.lock:
            prev = self.phase
            self.phase = phase
            if message:
                self.last_error = message
        if prev != phase:
            label = PHASE_USER_LABELS.get(phase.value, phase.value)
            parts = [f"PHASE → {phase.value}", label]
            if detail:
                parts.append(detail)
            elif message:
                parts.append(message)
            self.append_activity(" | ".join(parts))

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            phase_val = self.phase.value
            return {
                "phase": phase_val,
                "phase_label": PHASE_USER_LABELS.get(phase_val, phase_val),
                "scan_cancel_pending": self.scan_cancel_requested,
                "last_error": self.last_error,
                "device": self.device_info,
                "mount_udid": self.mount_udid,
                "mount_path": str(self.mount_path) if self.mount_path else None,
                "scan_artifact_path": str(self.scan_artifact_path) if self.scan_artifact_path else None,
                "active_scan_session_id": self.active_scan_session_id,
                "active_exact_scan_session_id": self.active_exact_scan_session_id,
                "active_fuzzy_scan_session_id": self.active_fuzzy_scan_session_id,
                "scan_running_kind": self.scan_running_kind,
                "fuzzy_roll_next_start": self.fuzzy_roll_next_start,
                "fuzzy_roll_total": self.fuzzy_roll_total,
                "fuzzy_roll_exhausted": bool(
                    self.fuzzy_roll_total is not None
                    and self.fuzzy_roll_total > 0
                    and self.fuzzy_roll_next_start >= self.fuzzy_roll_total
                ),
                "library_indexed_count": self.library_indexed_count,
                "group_count": len(self.duplicate_groups),
                "exact_group_count": sum(
                    1 for g in self.duplicate_groups if str(g.get("scan_kind") or "exact") != "fuzzy"
                ),
                "fuzzy_group_count": sum(
                    1 for g in self.duplicate_groups if str(g.get("scan_kind") or "exact") == "fuzzy"
                ),
                "jobs": [j.to_dict() for j in self.jobs.values()],
                "last_delete_ledger": self.last_delete_ledger,
                "document_last_ledger": self.document_last_ledger,
                "activity_log": list(self.activity_log),
            }

    def _activity_log_append_nolock(self, message: str) -> None:
        if not message:
            return
        self.activity_log_seq += 1
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts} #{self.activity_log_seq:05d}] {message}"
        self.activity_log.append(line)
        log_activity(line)

    def append_activity(self, message: str) -> None:
        """Append one timestamped line to the UI activity log (thread-safe)."""
        with self.lock:
            self._activity_log_append_nolock(message)

    def clear_activity_log(self) -> None:
        """Clear the on-screen activity log (operator-controlled reset)."""
        with self.lock:
            self.activity_log.clear()

    def start_job(self, kind: str, label: str) -> JobStatus:
        jid = uuid.uuid4().hex[:12]
        job = JobStatus(job_id=jid, kind=kind, label=label, running=True, message="Starting…")
        with self.lock:
            self.jobs[jid] = job
            self._activity_log_append_nolock(
                "==== NEW BACKGROUND JOB =================================================="
            )
            self._activity_log_append_nolock(
                f"JOB START | job_id={jid} | kind={kind} | label={label!r} | message=Starting…"
            )
            self._activity_log_append_nolock(
                f"SESSION CONTEXT | phase={self.phase.value} | mount_path="
                f"{str(self.mount_path) if self.mount_path else '(none)'} | mount_udid={self.mount_udid or '(none)'} "
                f"| duplicate_groups={len(self.duplicate_groups)} | scan_cancel_pending={self.scan_cancel_requested}"
            )
        return job

    def update_job(
        self,
        job_id: str,
        message: str,
        *,
        progress_current: int | None | object = _MISSING,
        progress_total: int | None | object = _MISSING,
    ) -> None:
        with self.lock:
            j = self.jobs.get(job_id)
            if j:
                j.message = message
                if progress_current is not _MISSING:
                    j.progress_current = progress_current  # type: ignore[assignment]
                if progress_total is not _MISSING:
                    j.progress_total = progress_total  # type: ignore[assignment]
                if message:
                    extra = ""
                    if progress_current is not _MISSING and progress_total is not _MISSING:
                        pc = progress_current  # type: ignore[assignment]
                        pt = progress_total  # type: ignore[assignment]
                        if isinstance(pc, int) and isinstance(pt, int) and pt > 0:
                            pct = min(100.0, max(0.0, 100.0 * pc / pt))
                            extra = f" | progress={pc}/{pt} ({pct:.1f}%)"
                    self._activity_log_append_nolock(
                        f"job={job_id} kind={j.kind} UPDATE | {message}{extra}"
                    )

    def finish_job(self, job_id: str, message: str = "") -> None:
        with self.lock:
            j = self.jobs.get(job_id)
            if j:
                j.running = False
                j.finished_at = time.time()
                j.progress_current = None
                j.progress_total = None
                if message:
                    j.message = message
                fin_msg = message or "(no summary message)"
                dur = ""
                if j.finished_at and j.started_at:
                    dur = f" | duration_sec={j.finished_at - j.started_at:.2f}"
                self._activity_log_append_nolock(f"job={job_id} kind={j.kind} FINISHED | {fin_msg}{dur}")
