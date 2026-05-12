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

    def effective_keep_mode(self) -> str:
        if self.state.runtime_keep_mode:
            return self.state.runtime_keep_mode
        return self.settings.duplicates_keep_mode
