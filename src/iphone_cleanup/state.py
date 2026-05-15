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
    duplicate_groups: list[dict[str, Any]] = field(default_factory=list)
    group_keep: dict[str, list[str]] = field(default_factory=dict)
    jobs: dict[str, JobStatus] = field(default_factory=dict)
    event_seq: int = 0
    scan_cancel_event: threading.Event = field(default_factory=threading.Event)
    scan_cancel_requested: bool = False
    # Fuzzy roll: next slice start index into the cached sorted library; total filled after first batch load.
    fuzzy_roll_next_start: int = 0
    fuzzy_roll_total: int | None = None
    pending_rescan_kind: str | None = None
    last_delete_ledger: dict[str, Any] | None = None
    document_last_ledger: dict[str, Any] | None = None
    # Rolling verbose trace for the UI (timestamps + monotonic line numbers).
    activity_log: deque[str] = field(default_factory=lambda: deque(maxlen=512))
    activity_log_seq: int = 0

    def next_event_seq(self) -> int:
        with self.lock:
            self.event_seq += 1
            return self.event_seq

    def set_phase(self, phase: Phase, message: str = "") -> None:
        with self.lock:
            self.phase = phase
            if message:
                self.last_error = message

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "phase": self.phase.value,
                "scan_cancel_pending": self.scan_cancel_requested,
                "last_error": self.last_error,
                "device": self.device_info,
                "mount_udid": self.mount_udid,
                "mount_path": str(self.mount_path) if self.mount_path else None,
                "scan_artifact_path": str(self.scan_artifact_path) if self.scan_artifact_path else None,
                "fuzzy_roll_next_start": self.fuzzy_roll_next_start,
                "fuzzy_roll_total": self.fuzzy_roll_total,
                "fuzzy_roll_exhausted": bool(
                    self.fuzzy_roll_total is not None
                    and self.fuzzy_roll_total > 0
                    and self.fuzzy_roll_next_start >= self.fuzzy_roll_total
                ),
                "group_count": len(self.duplicate_groups),
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
