"""In-memory session state and background job bookkeeping."""

from __future__ import annotations

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Phase(str, enum.Enum):
    idle = "idle"
    device_detected = "device_detected"
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
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "label": self.label,
            "running": self.running,
            "message": self.message,
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
    group_keep: dict[str, str] = field(default_factory=dict)
    jobs: dict[str, JobStatus] = field(default_factory=dict)
    event_seq: int = 0
    runtime_keep_mode: str | None = None
    scan_cancel_event: threading.Event = field(default_factory=threading.Event)
    last_delete_ledger: dict[str, Any] | None = None
    document_last_ledger: dict[str, Any] | None = None

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
                "last_error": self.last_error,
                "device": self.device_info,
                "mount_udid": self.mount_udid,
                "mount_path": str(self.mount_path) if self.mount_path else None,
                "scan_artifact_path": str(self.scan_artifact_path) if self.scan_artifact_path else None,
                "group_count": len(self.duplicate_groups),
                "jobs": [j.to_dict() for j in self.jobs.values()],
                "last_delete_ledger": self.last_delete_ledger,
                "document_last_ledger": self.document_last_ledger,
            }

    def start_job(self, kind: str, label: str) -> JobStatus:
        jid = uuid.uuid4().hex[:12]
        job = JobStatus(job_id=jid, kind=kind, label=label, running=True, message="Starting…")
        with self.lock:
            self.jobs[jid] = job
        return job

    def update_job(self, job_id: str, message: str) -> None:
        with self.lock:
            j = self.jobs.get(job_id)
            if j:
                j.message = message

    def finish_job(self, job_id: str, message: str = "") -> None:
        with self.lock:
            j = self.jobs.get(job_id)
            if j:
                j.running = False
                j.finished_at = time.time()
                if message:
                    j.message = message
