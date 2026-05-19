"""Application context shared by HTTP layer and services."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from iphone_cleanup.settings import Settings
from iphone_cleanup.state import AppState


@dataclass
class AppCtx:
    settings: Settings
    state: AppState
    no_open_browser: bool
    thumb_semaphore: threading.BoundedSemaphore
    run_session_id: str = ""

    def effective_keep_mode(self) -> str:
        """Compatibility shim for API payloads; keeper picks always start from auto ranking."""
        return "auto_best"
